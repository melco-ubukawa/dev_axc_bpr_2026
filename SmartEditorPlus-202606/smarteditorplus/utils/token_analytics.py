import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from .storage import get_blob_service_client, AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME

logger = logging.getLogger(__name__)

# モデルごとの価格設定 (100万トークンあたりのドル単価)
# ユーザー提供情報および公式URLから取得した値を定義
MODEL_PRICING = {
    "gemini-flash-latest":        {"input": 0.30, "output": 2.50},
    "gemini-flash-lite-latest":   {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-image":     {"input": 0.30, "output": 30.00}, # 画像出力トークン換算
    "gemini-2.5-flash":           {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite":      {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-tts":       {"input": 0.50, "output": 10.00},
    "gemini-2.5-pro":             {"input": 1.25, "output": 10.00},
    "gemini-3-flash":             {"input": 0.50, "output": 3.00},
    "gemini-3-pro-image-preview": {"input": 2.00, "output": 12.00}, # テキストベース
    "gemini-3-pro-preview":       {"input": 2.00, "output": 12.00},
    "gpt-5":                      {"input": 1.25, "output": 10.00, "cached_input": 0.13}, # ユーザー提供値
    # デフォルト（不明なモデル用）
    "default":                    {"input": 0.00, "output": 0.00}
}

def calculate_cost(model_name: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    """
    モデル名とトークン数から推定コスト（ドル）を算出する
    """
    # モデル名が部分的（バージョン番号違いなど）でもマッチするように調整
    pricing = MODEL_PRICING.get("default")
    matched_key = "default"
    
    # 完全一致または部分一致で価格設定を探す
    if model_name in MODEL_PRICING:
        pricing = MODEL_PRICING[model_name]
        matched_key = model_name
    else:
        for key, val in MODEL_PRICING.items():
            if key in model_name:
                pricing = val
                matched_key = key
                break
    
    # 100万トークン単位での計算
    # 入力コスト (キャッシュ分がある場合は、それを差し引くロジックが必要だが、
    # prompt_tokensにキャッシュ分が含まれているかどうかの仕様による。
    # ここでは prompt_tokens は「総入力」とし、もし cached_tokens が別計上なら
    # 単価を変えて計算するアプローチをとる)
    
    input_price = pricing["input"]
    output_price = pricing["output"]
    cached_input_price = pricing.get("cached_input", 0.0)

    # GPT-5などのキャッシュロジック
    # ログに cached_tokens が記録されており、かつ prompt_tokens にそれが含まれていると仮定した場合の計算
    # 通常の入力分 = prompt_tokens - cached_tokens
    normal_input_tokens = max(0, prompt_tokens - cached_tokens)
    
    input_cost = (normal_input_tokens / 1_000_000) * input_price
    cached_cost = (cached_tokens / 1_000_000) * cached_input_price
    output_cost = (completion_tokens / 1_000_000) * output_price
    
    total_cost = input_cost + cached_cost + output_cost
    return round(total_cost, 6)

def _process_single_log_blob(blob_client):
    """
    単一のBlob(JSON)をダウンロードして必要な情報を抽出するヘルパー関数
    """
    try:
        # Blobデータをダウンロード
        stream = blob_client.download_blob()
        data = json.loads(stream.readall())

        # 必要な情報を抽出
        # parameters.modelName がなければ unknown とする
        model_name = data.get("parameters", {}).get("modelName", "unknown")
        usage = data.get("usage", {})

        # ユーザー情報・機能カテゴリ（なければデフォルト値）
        user_id = data.get("userId") or "unknown_user"
        user_name = data.get("userName") or ""
        user_email = data.get("userEmail") or ""
        feature_category = data.get("featureCategory") or "未分類"
        
        # usage情報がない、または0の場合はスキップ（コスト計算できないため）
        if not usage:
            return None

        # .get() が None を返した場合に備えて 'or 0' を追加
        return {
            "model": model_name,
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
            # 将来的にログにキャッシュトークン数が含まれる場合に対応
            "cached_tokens": usage.get("cached_tokens") or 0,
            "user_id": user_id,
            "user_name": user_name,
            "user_email": user_email,
            "feature_category": feature_category,
        }
    except Exception as e:
        logger.warning(f"Failed to process log blob {blob_client.blob_name}: {e}")
        return None

def aggregate_token_usage(start_date: datetime, end_date: datetime):
    """
    指定期間内のActivity Logを集計する
    """
    client = get_blob_service_client()
    if not client:
        return {"error": "Storage client not initialized"}

    container_client = client.get_container_client(AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME)
    
    # 集計結果の格納用辞書
    # モデル別: { "model_name": { "prompt": 0, "completion": 0, "total": 0, "cached": 0, "count": 0 } }
    stats_by_model = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "count": 0})

    # ユーザー別: { "user_id": { "userId": .., "userName": .., "userEmail": .., "prompt": .., ..., "by_model": { model: {...} } } }
    stats_by_user = defaultdict(
        lambda: {
            "userId": None,
            "userName": None,
            "userEmail": None,
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "cached": 0,
            "count": 0,
            "by_model": defaultdict(lambda: {"prompt": 0, "completion": 0, "cached": 0}),
        }
    )

    # 機能別: { "feature_category": { "featureCategory": .., "prompt": .., ..., "by_model": { model: {...} } } }
    stats_by_feature = defaultdict(
        lambda: {
            "featureCategory": None,
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "cached": 0,
            "count": 0,
            "by_model": defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "count": 0}),
        }
    )
    
    # 検索対象のフォルダ構成:(activity-log 専用コンテナ内) {category}/{YYYY}/{MM}/{DD}/{HH}/{log_id}.json
    # 日付ごとにプレフィックス検索を行うことで、期間外のファイルをスキャンするコストを下げる
    
    target_date_paths = []
    current_date = start_date
    while current_date <= end_date:
        # 日付パス部分: YYYY/MM/DD
        date_path = current_date.strftime('%Y/%m/%d')
        target_date_paths.append(date_path)
        current_date += timedelta(days=1)

    # コンテナ内のBlobをリストアップ
    # 注意: Blob Storageは階層型ではないため、プレフィックスで絞り込むのが基本。
    # しかし、カテゴリごとのフォルダがあるため、"activity-logs/" で全件取得してから
    # 日付文字列でフィルタリングする方が、カテゴリを網羅する上で実装が容易。
    # (ログが膨大な場合は、カテゴリごとにループして日付プレフィックスを指定する等の最適化が必要)
    blobs = container_client.list_blobs()
    
    target_blobs = []
    for blob in blobs:
        # Blob名に日付パスが含まれているか確認
        # 例: chat/2025/11/26/10/xxxx.json -> "2025/11/26" が含まれるか
        if any(d in blob.name for d in target_date_paths):
            target_blobs.append(blob)

    logger.info(f"Found {len(target_blobs)} logs to process for period {start_date.date()} to {end_date.date()}.")

    # 並列処理でJSONをダウンロード・解析
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 各Blobに対してクライアントを取得してタスク化
        futures = [
            executor.submit(_process_single_log_blob, container_client.get_blob_client(b.name))
            for b in target_blobs
        ]
        
        for future in as_completed(futures):
            result = future.result()
            if not result:
                continue

            model_name = result["model"]
            prompt_tokens = result["prompt_tokens"]
            completion_tokens = result["completion_tokens"]
            total_tokens = result["total_tokens"]
            cached_tokens = result["cached_tokens"]

            user_id = result.get("user_id") or "unknown_user"
            user_name = result.get("user_name") or ""
            user_email = result.get("user_email") or ""
            feature_category = result.get("feature_category") or "未分類"

            # --- モデル別集計 ---
            stats_by_model[model_name]["prompt"] += prompt_tokens
            stats_by_model[model_name]["completion"] += completion_tokens
            stats_by_model[model_name]["total"] += total_tokens
            stats_by_model[model_name]["cached"] += cached_tokens
            stats_by_model[model_name]["count"] += 1

            # --- ユーザー別集計 ---
            user_stats = stats_by_user[user_id]
            user_stats["userId"] = user_id
            user_stats["userName"] = user_name
            user_stats["userEmail"] = user_email
            user_stats["prompt"] += prompt_tokens
            user_stats["completion"] += completion_tokens
            user_stats["total"] += total_tokens
            user_stats["cached"] += cached_tokens
            user_stats["count"] += 1

            user_model_stats = user_stats["by_model"][model_name]
            user_model_stats["prompt"] += prompt_tokens
            user_model_stats["completion"] += completion_tokens
            user_model_stats["cached"] += cached_tokens

            # --- 機能別集計 ---
            feature_key = feature_category
            feature_stats = stats_by_feature[feature_key]
            feature_stats["featureCategory"] = feature_key
            feature_stats["prompt"] += prompt_tokens
            feature_stats["completion"] += completion_tokens
            feature_stats["total"] += total_tokens
            feature_stats["cached"] += cached_tokens
            feature_stats["count"] += 1

            feature_model_stats = feature_stats["by_model"][model_name]
            feature_model_stats["prompt"] += prompt_tokens
            feature_model_stats["completion"] += completion_tokens
            feature_model_stats["total"] += total_tokens
            feature_model_stats["cached"] += cached_tokens
            feature_model_stats["count"] += 1

    # 結果をリスト形式に整形し、コストを計算
    usage_by_model = []
    usage_by_user = []
    usage_by_feature = []
    usage_by_feature_model = []

    total_estimated_cost = 0.0

    # --- モデル別 ---
    for model, data in stats_by_model.items():
        estimated_cost = calculate_cost(
            model,
            data["prompt"],
            data["completion"],
            data["cached"],
        )
        total_estimated_cost += estimated_cost

        usage_by_model.append({
            "model": model,
            "prompt_tokens": data["prompt"],
            "completion_tokens": data["completion"],
            "total_tokens": data["total"],
            "cached_tokens": data["cached"],
            "request_count": data["count"],
            "estimated_cost_usd": estimated_cost,
        })

    usage_by_model.sort(key=lambda x: x["model"])

    # --- ユーザー別 ---
    for user_id, data in stats_by_user.items():
        # モデル別トークン数からコストを算出して合計
        user_cost = 0.0
        for model_name, m_tokens in data["by_model"].items():
            user_cost += calculate_cost(
                model_name,
                m_tokens["prompt"],
                m_tokens["completion"],
                m_tokens["cached"],
            )

        usage_by_user.append({
            "userId": data["userId"] or user_id,
            "userName": data["userName"],
            "userEmail": data["userEmail"],
            "prompt_tokens": data["prompt"],
            "completion_tokens": data["completion"],
            "total_tokens": data["total"],
            "cached_tokens": data["cached"],
            "request_count": data["count"],
            "estimated_cost_usd": round(user_cost, 6),
        })

    # コストの高い順でソート
    usage_by_user.sort(key=lambda x: x["estimated_cost_usd"], reverse=True)

    # --- 機能別 ---
    for feature_key, data in stats_by_feature.items():
        feature_cost = 0.0
        for model_name, m_tokens in data["by_model"].items():
            feature_cost += calculate_cost(
                model_name,
                m_tokens["prompt"],
                m_tokens["completion"],
                m_tokens["cached"],
            )

            usage_by_feature_model.append({
                "feature_category": data["featureCategory"] or feature_key,
                "model": model_name,
                "prompt_tokens": m_tokens["prompt"],
                "completion_tokens": m_tokens["completion"],
                "total_tokens": m_tokens["total"],
                "cached_tokens": m_tokens["cached"],
                "request_count": m_tokens["count"],
                "estimated_cost_usd": calculate_cost(
                    model_name,
                    m_tokens["prompt"],
                    m_tokens["completion"],
                    m_tokens["cached"],
                ),
            })

        usage_by_feature.append({
            "feature_category": data["featureCategory"] or feature_key,
            "prompt_tokens": data["prompt"],
            "completion_tokens": data["completion"],
            "total_tokens": data["total"],
            "cached_tokens": data["cached"],
            "request_count": data["count"],
            "estimated_cost_usd": round(feature_cost, 6),
        })

    usage_by_feature.sort(key=lambda x: x["estimated_cost_usd"], reverse=True)

    # コストの高い順（同率なら機能→モデル）
    usage_by_feature_model.sort(key=lambda x: (-x["estimated_cost_usd"], x["feature_category"], x["model"]))

    return {
        "period": {
            "start": start_date.strftime('%Y-%m-%d'),
            "end": end_date.strftime('%Y-%m-%d'),
        },
        "total_estimated_cost_usd": round(total_estimated_cost, 6),
        "usage_by_model": usage_by_model,
        "usage_by_user": usage_by_user,
        "usage_by_feature": usage_by_feature,
        "usage_by_feature_model": usage_by_feature_model,
    }