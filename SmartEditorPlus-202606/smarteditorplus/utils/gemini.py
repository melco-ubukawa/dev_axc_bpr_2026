import os
import uuid
import logging
import base64
import copy
from datetime import datetime, timezone
from google import genai
from google.genai import types
from azure.cosmos import exceptions as cosmos_exceptions
from openai import AzureOpenAI
from .storage import (upload_bytes_to_blob, get_blob_sas_url, add_usage_to_blob_json, AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME)
import json
import config
import fitz
import time
import random
from flask import has_request_context, g

# --- グローバル変数 ---
gemini_client = None  # 単一構成時の従来クライアント
gemini_client_pool = {}  # 複数プロジェクト構成時: { api_name: genai.Client }
azure_openai_client = None
cosmos_gemini_log_container = None

# --- ロガー設定 ---
logger = logging.getLogger(__name__)

def _build_log_entry(
    call_timestamp: datetime,
    user_info: dict,
    model_name: str,
    system_instruction: str | None,
    log_prompt_text: str | None,
    log_filenames: list[str] | None,
    max_output_tokens: int,
    temperature: float,
    feature_category: str,
    additional_params: dict | None = None
) -> dict:
    """ログエントリの共通データを構築するヘルパー関数。"""
    user_id = user_info.get("userId") if user_info else "unknown_user_id"
    user_name = user_info.get("userName") if user_info else None
    user_email = user_info.get("userEmail") if user_info else None

    log_entry = {
        "id": str(uuid.uuid4()),
        "timestamp": call_timestamp.isoformat(),
        "userId": user_id,
        "userName": user_name,
        "userEmail": user_email,
        "featureCategory": feature_category,
        "calledFunction": "generate_with_gemini",
        "parameters": {
            "modelName": model_name,
            "systemInstruction": system_instruction,
            "userPrompt": log_prompt_text,
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
        },
        "inputDetails": {
            "attachedFilenames": log_filenames if log_filenames else [],
        }
    }

    # どのGeminiターゲット(GOOGLE_CLOUD_NAME / GOOGLE_CLOUD_NAME_2 等)を利用したかを付与
    # requestコンテキスト外では取得できないため、取得できた場合のみ付与する
    try:
        from flask import has_request_context, g as flask_g  # 局所importで循環参照を避ける

        google_cloud_name = None
        if has_request_context():
            google_cloud_name = getattr(flask_g, "google_cloud_name", None)

        if google_cloud_name:
            # ここでは「どのターゲットか」の名前だけを記録する
            # （実際の Project / Location は不要との要件に合わせる）
            log_entry["parameters"]["googleCloudName"] = google_cloud_name
    except Exception:
        # ログ用の付加情報取得なので、失敗しても処理は継続する
        pass

    if additional_params:
        log_entry["parameters"].update(additional_params)
    
    return log_entry

def _create_gemini_client(project_id: str, location: str):
    """与えられたプロジェクト/ロケーションでGeminiクライアントを生成するヘルパー。"""
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
    )


# --- Gemini クライアントの取得（単一/複数プロジェクト共通） ---
def get_gemini_client():
    """Geminiクライアントを取得する。

    - config.GEMINI_API_TARGETS が定義されていれば、その中から1つを選択
      （HTTPリクエスト中であれば flask.g.google_cloud_name に従う）。
    - それ以外の場合は、従来通り GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION
      に基づく単一クライアントを返す。
    """
    global gemini_client, gemini_client_pool

    targets = getattr(config, "GEMINI_API_TARGETS", [])

    # --- 複数ターゲットが構成されている場合（分散モード） ---
    if targets:
        api_name = None

        if has_request_context():
            # HTTPリクエスト内では、before_request で選択されたAPI名を優先
            api_name = getattr(g, "google_cloud_name", None)
            if api_name is None and targets:
                chosen = random.choice(targets)
                api_name = chosen["name"]
                g.google_cloud_name = api_name
        else:
            # リクエスト外（バッチ等）の場合はその都度ランダム選択
            chosen = random.choice(targets)
            api_name = chosen["name"]

        # api_name に対応するターゲット情報を取得
        target = next((t for t in targets if t["name"] == api_name), None)
        if target is None:
            # 万一 name が不整合な場合は先頭要素にフォールバック
            target = targets[0]
            api_name = target["name"]

        if api_name not in gemini_client_pool:
            project_id = target["project"]
            location = target["location"]
            logger.info(f"Initializing Gemini client for target '{api_name}' (project={project_id}, location={location})...")
            try:
                gemini_client_pool[api_name] = _create_gemini_client(project_id, location)
                logger.info(f"Gemini client for '{api_name}' initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client for target '{api_name}': {e}", exc_info=True)
                return None

        return gemini_client_pool[api_name]

    # --- 従来の単一ターゲット構成（後方互換） ---
    if gemini_client is None:
        try:
            project_id = getattr(config, "DEFAULT_GEMINI_PROJECT", None) or os.environ.get("GOOGLE_CLOUD_PROJECT")
            location = getattr(config, "DEFAULT_GEMINI_LOCATION", None) or os.environ.get("GOOGLE_CLOUD_LOCATION")
            if not project_id or not location:
                logger.error("GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION is not set.")
                return None
            logger.info("Attempting to initialize Gemini client (single-target mode)...")
            gemini_client = _create_gemini_client(project_id, location)
            logger.info("Gemini client initialized successfully (single-target mode).")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
            return None
    return gemini_client

# --- Azure OpenAI クライアントの初期化 ---
def get_azure_openai_client():
    """Azure OpenAIクライアントをシングルトンとして取得・初期化する。"""
    global azure_openai_client
    if azure_openai_client is None:
        try:
            if not all([config.AZURE_OPENAI_ENDPOINT, config.AZURE_OPENAI_API_KEY]):
                logger.error("Azure OpenAI のエンドポイントまたはAPIキーが設定されていません。")
                return None
            
            logger.info("Azure OpenAI クライアントを初期化しています...")
            azure_openai_client = AzureOpenAI(
                api_key=config.AZURE_OPENAI_API_KEY,
                api_version=config.AZURE_OPENAI_API_VERSION,
                azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            )
            logger.info("Azure OpenAI クライアントの初期化に成功しました。")
        except Exception as e:
            logger.error(f"Azure OpenAI クライアントの初期化に失敗: {e}", exc_info=True)
            return None
    return azure_openai_client

# --- Blob StorageへのAI API呼び出しログ記録関数 ---
def log_gemini_call_to_cosmos(
    container,
    call_timestamp: datetime,
    user_info: dict,
    model_name: str,
    system_instruction: str | None,
    log_prompt_text: str | None,
    log_filenames: list[str] | None,
    max_output_tokens: int,
    temperature: float,
    feature_category: str,
    additional_params: dict | None = None
) -> str | None: # 戻り値の型ヒントを変更
    """
    AIモデルの呼び出し情報をAzure Blob Storageに記録する。
    ★変更点: 保存したBlobの名前（パス）を返すように変更。
    """

    # ログエントリの共通部分を構築
    log_entry = _build_log_entry(
        call_timestamp, user_info, model_name, system_instruction,
        log_prompt_text, log_filenames, max_output_tokens, temperature,
        feature_category, additional_params
    )

    try:
        log_time = call_timestamp
        # カテゴリ名に日本語や特殊文字が含まれる可能性があるため、URLセーフな名前に置換
        safe_category_name = feature_category.replace("（", "_").replace("）", "").replace("/", "_")
        
        blob_name = (
            f"{safe_category_name}/"
            f"{log_time.strftime('%Y/%m/%d/%H')}/"
            f"{log_entry['id']}.json"
        )
        
        log_json_string = json.dumps(log_entry, indent=2, ensure_ascii=False)
        log_bytes = log_json_string.encode('utf-8')
        
        upload_url = upload_bytes_to_blob(
            log_bytes,
            blob_name,
            container_name=AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        )
        
        if upload_url:
            logger.info(f"User activity log recorded: {blob_name}")
            logger.debug(f"User activity log content for {blob_name}: {log_json_string}")
            return blob_name # ★ Blob名を返す
        else:
            logger.error(f"Failed to record user activity log: {blob_name}")
            return None
            
    except Exception as e:
        logger.error(f"Unexpected error while recording log: {e}", exc_info=True)
        return None

def update_gemini_log_usage(blob_name: str, usage_data: dict):
    """
    ★新規追加: ログファイルにトークン使用量を追記する
    """
    if not blob_name:
        return

    try:
        # どのGeminiターゲット(api1/api2)を利用したかも、usageと一緒に記録する
        google_cloud_name = None
        try:
            if has_request_context():
                google_cloud_name = getattr(g, "google_cloud_name", None)
        except Exception:
            # ログ用途なので、この取得に失敗しても処理は継続する
            google_cloud_name = None

        extra_updates = {}
        if google_cloud_name is not None:
            # usage 追記ではスネークケースで保持
            extra_updates["google_cloud_name"] = google_cloud_name

        # 重要: 既存usage(サブ呼び出し加算分)を上書きしないよう「加算マージ」で更新
        add_usage_to_blob_json(
            blob_name,
            usage_data,
            extra_updates=extra_updates,
            container_name=AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        )
        logger.debug(f"Updated usage(additive) for log {blob_name}: usage={usage_data}, extra={extra_updates}")
    except Exception as e:
        logger.error(f"Failed to update usage for log {blob_name}: {e}", exc_info=True)


def ensure_parent_log_blob_name(
    *,
    user_info: dict | None,
    feature_category: str,
    model_name: str,
    additional_log_params: dict | None = None,
) -> str | None:
    """サブ呼び出しを親ログに集約するための親Blob名を確保する。

    - リクエストコンテキスト内なら g.parent_log_blob_name を再利用
    - 未設定なら、最小情報のログBlobを1件作成して g.parent_log_blob_name に保存
    """
    try:
        if has_request_context():
            existing = getattr(g, "parent_log_blob_name", None)
            if existing:
                return existing
    except Exception:
        pass

    from .cosmos_db import get_cosmos_container

    call_time = datetime.now(timezone.utc)
    log_container = get_cosmos_container()

    blob_name = log_gemini_call_to_cosmos(
        container=log_container,
        call_timestamp=call_time,
        user_info=user_info if user_info else {},
        model_name=model_name,
        system_instruction=None,
        log_prompt_text=None,
        log_filenames=None,
        max_output_tokens=0,
        temperature=0.0,
        feature_category=feature_category,
        additional_params=additional_log_params,
    )

    try:
        if has_request_context() and blob_name and not getattr(g, "parent_log_blob_name", None):
            g.parent_log_blob_name = blob_name
    except Exception:
        pass

    return blob_name

# --- Azure OpenAI APIを呼び出す内部関数 ---
def _generate_with_azure_openai(
    model_name: str,
    contents: list[types.Content],
    system_instruction: str | None,
    max_output_tokens: int,
    temperature: float,
    top_p: float | None,
    stream: bool,
    usage_log_blob_name: str | None = None,
    debug_log_on_parent_usage_aggregation: bool = False,
    debug_parent_log_blob_name: str | None = None,
    debug_feature_category: str | None = None,
):
    """
    Azure OpenAI APIを呼び出す内部関数。
    (画像とテキストのマルチモーダル入力に対応)
    ★修正: 推論モデル(gpt-5/o1/o3)の場合、temperature/top_pの指定を除外する処理を追加
    """
    client = get_azure_openai_client()
    if not client:
        error_message = "[エラー: Azure OpenAIクライアントの初期化に失敗しました。]"
        if stream:
            def error_stream(): yield type('obj', (object,), {'text': error_message})
            return error_stream()
        return error_message, None

    messages = []
    if system_instruction:
        # o1-previewなどはsystemロールをdeveloperロールとして扱う場合があるが、
        # Azure OpenAIのバージョンによってはsystemで通るため、一旦systemのままにする
        # 必要に応じて "role": "developer" への変換ロジックを入れる
        messages.append({"role": "system", "content": system_instruction})

    # Geminiの'contents'形式からAzure OpenAIの'messages'形式へ変換
    for content in contents:
        role = "assistant" if content.role == "model" else "user"
        
        message_content_parts = []
        
        # テキストパートを抽出
        text_parts = [part.text for part in content.parts if hasattr(part, 'text') and part.text]
        if text_parts:
            full_text = " ".join(text_parts)
            message_content_parts.append({"type": "text", "text": full_text})

        # 画像データ(inline_data)を処理
        for part in content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                mime_type = part.inline_data.mime_type
                # 画像形式のみをBase64にエンコードして渡す
                if mime_type.startswith("image/"):
                    data = part.inline_data.data
                    base64_data = base64.b64encode(data).decode('utf-8')
                    message_content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}
                    })
                else:
                    logger.warning(f"Unsupported inline_data MIME type for Azure OpenAI: {mime_type}. Skipping.")

        if not message_content_parts:
            continue
            
        final_content = message_content_parts
        if len(final_content) == 1 and final_content[0]['type'] == 'text':
            final_content = final_content[0]['text']
        
        messages.append({"role": role, "content": final_content})
            
    deployment_name = config.AZURE_OPENAI_DEPLOYMENTS.get(model_name)
    if not deployment_name:
        error_message = f"[エラー: モデル '{model_name}' に対応するAzureデプロイメント名が見つかりません。]"
        if stream:
            def error_stream(): yield type('obj', (object,), {'text': error_message})
            return error_stream()
        return error_message, None

    try:
        # 推論モデルかどうかの判定 (gpt-5, o1, o3 が含まれる場合)
        # これらは temperature=1 固定等の制約がある場合が多い
        is_reasoning_model = any(x in model_name.lower() for x in ["gpt-5", "o1", "o3"])

        api_params = {
            "model": deployment_name,
            "messages": messages,
            "stream": stream,
        }

        # 推論モデル以外の場合のみ temperature / top_p を設定する
        if not is_reasoning_model:
            api_params["temperature"] = temperature
            if top_p is not None:
                api_params["top_p"] = top_p
        else:
            # 推論モデルの場合、ログに出しておく
            logger.info(f"Reasoning model detected ({model_name}). Skipping temperature/top_p parameters.")

        # トークン数制限のパラメータ名の振り分け
        # gpt-4oやo1/o3系は max_completion_tokens が推奨される傾向にある
        if is_reasoning_model or "gpt-4o" in model_name.lower():
             api_params["max_completion_tokens"] = max_output_tokens
        else:
             api_params["max_tokens"] = max_output_tokens
        
        # ストリーミング時にもUsage情報を取得するオプション
        if stream:
            api_params["stream_options"] = {"include_usage": True}
        
        response = client.chat.completions.create(**api_params)

        if stream:
            def stream_wrapper():
                final_usage = None
                try:
                    for chunk in response:
                        # Usage情報の取得 (ストリームの最後のチャンクに含まれる)
                        if hasattr(chunk, 'usage') and chunk.usage:
                            final_usage = chunk.usage
                        
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                            yield type('obj', (object,), {'text': chunk.choices[0].delta.content})
                finally:
                    # ストリーム終了後にログエントリを更新してトークン数を記録
                    if usage_log_blob_name and final_usage:
                        usage_data = {
                            "prompt_tokens": final_usage.prompt_tokens,
                            "completion_tokens": final_usage.completion_tokens,
                            "total_tokens": final_usage.total_tokens
                        }
                        update_gemini_log_usage(usage_log_blob_name, usage_data)
                        if debug_log_on_parent_usage_aggregation:
                            logger.info(
                                "Aggregated usage to parent log (skip_cosmos_log=True, Azure OpenAI). "
                                f"feature_category={debug_feature_category}, model={model_name}, "
                                f"parent_blob={debug_parent_log_blob_name}, usage={usage_data}"
                            )

            return stream_wrapper()
        else:
            full_text = response.choices[0].message.content or ""
            
            # 非ストリーム時のログ更新
            if usage_log_blob_name and response.usage:
                usage_data = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
                update_gemini_log_usage(usage_log_blob_name, usage_data)
                if debug_log_on_parent_usage_aggregation:
                    logger.info(
                        "Aggregated usage to parent log (skip_cosmos_log=True, Azure OpenAI). "
                        f"feature_category={debug_feature_category}, model={model_name}, "
                        f"parent_blob={debug_parent_log_blob_name}, usage={usage_data}"
                    )

            return full_text.strip(), response.usage

    except Exception as e:
        logger.error(f"Azure OpenAI API呼び出し中にエラー: {e}", exc_info=True)
        error_message = f"[APIエラー (Azure): {type(e).__name__} - {e}]"
        if stream:
            def error_stream(): yield type('obj', (object,), {'text': error_message})
            return error_stream()
        return error_message, None


def _extract_text_from_pdf_part(pdf_part: types.Part) -> types.Part | None:
    """PDFのバイナリデータを含むPartからテキストを抽出し、新しいテキストPartを返す"""
    if not (hasattr(pdf_part, 'inline_data') and pdf_part.inline_data.mime_type == "application/pdf"):
        return None
    
    try:
        pdf_bytes = pdf_part.inline_data.data
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        
        # 抽出したテキストを新しいPartオブジェクトとして返す
        if full_text.strip():
            logger.info(f"Successfully extracted {len(full_text)} characters from PDF.")
            # 抽出したテキストであることがわかるようにヘッダーを付ける
            formatted_text = f"--- PDFから抽出したテキスト ---\n{full_text.strip()}\n--- テキストここまで ---"
            return types.Part.from_text(text=formatted_text)
        else:
            logger.warning("PDFからテキストを抽出しましたが、内容は空でした。")
            return types.Part.from_text(text="[PDFの内容は空です]")
            
    except Exception as e:
        logger.error(f"Failed to extract text from PDF bytes: {e}", exc_info=True)
        return types.Part.from_text(text="[エラー: PDFの解析に失敗しました]")

# --- 汎用AI呼び出しラッパー関数 ---
def generate_with_gemini(
    model_name: str,
    contents: list[types.Content],
    system_instruction: str | None = None,
    max_output_tokens: int = 100,
    temperature: float = 0.7,
    top_p: float | None = None,
    stream: bool = False,
    user_info: dict | None = None,
    log_prompt_text: str | None = None,
    log_filenames: list[str] | None = None,
    feature_category: str = "不明な機能",
    additional_log_params: dict | None = None,
    skip_cosmos_log: bool = False,
    use_grounding: bool = False,
    use_code_execution: bool = False,
    return_full_response: bool = False,
    urls_for_context: list[str] | None = None,
    generation_config_override: dict | None = None,
    max_output_tokens_override: int | None = None,
    aspect_ratio: str | None = None,
    parent_log_id: str | None = None  # ★ 追加: 親ログID
):
    """
    指定されたパラメータでAIモデルを呼び出す汎用ラッパー関数。
    モデル名に応じてGeminiまたはAzure OpenAIを呼び出す。
    トークン使用量の取得とログ更新ロジックを実装済み。
    ★★★ gemini-2.5-flash-image-preview モデルによる画像生成に対応 ★★★
    """
    
    # ログ保存用のBlob名を保持する変数
    log_blob_name = None

    # 親ログID（Blob名）を、リクエストコンテキストから自動継承
    effective_parent_log_id = parent_log_id
    try:
        if effective_parent_log_id is None and has_request_context():
            effective_parent_log_id = getattr(g, "parent_log_blob_name", None)
    except Exception:
        pass
    
    # 1. 先行ログ記録 (Blob名を保存)
    if not skip_cosmos_log:
        from .cosmos_db import get_cosmos_container
        call_time = datetime.now(timezone.utc)
        log_container = get_cosmos_container()
        
        log_params_to_use = additional_log_params.copy() if additional_log_params else {}
        log_params_to_use['use_grounding'] = use_grounding
        log_params_to_use['urls_for_context'] = urls_for_context or []
        
        # Blob名を戻り値として受け取る
        log_blob_name = log_gemini_call_to_cosmos(
            container=log_container, call_timestamp=call_time, user_info=user_info if user_info else {},
            model_name=model_name, system_instruction=system_instruction, log_prompt_text=log_prompt_text,
            log_filenames=log_filenames, max_output_tokens=max_output_tokens, temperature=temperature,
            feature_category=feature_category, additional_params=log_params_to_use
        )

        # このリクエスト内での「親ログ」を固定（最初に作られたBlob名を保持）
        try:
            if has_request_context() and log_blob_name and not getattr(g, "parent_log_blob_name", None):
                g.parent_log_blob_name = log_blob_name
                effective_parent_log_id = log_blob_name
        except Exception:
            pass

    # usageを書き込む先（サブ呼び出しは親に加算）
    usage_log_blob_name: str | None = None
    if log_blob_name:
        usage_log_blob_name = log_blob_name
    elif skip_cosmos_log and effective_parent_log_id:
        usage_log_blob_name = effective_parent_log_id

    debug_parent_aggregation = bool(skip_cosmos_log and usage_log_blob_name and effective_parent_log_id and usage_log_blob_name == effective_parent_log_id)

    def _update_usage_and_debug_if_needed(usage_data: dict, *, provider: str):
        if not usage_log_blob_name or not usage_data:
            return
        update_gemini_log_usage(usage_log_blob_name, usage_data)
        if debug_parent_aggregation:
            logger.info(
                "Aggregated usage to parent log (skip_cosmos_log=True). "
                f"provider={provider}, feature_category={feature_category}, model={model_name}, "
                f"parent_blob={effective_parent_log_id}, usage={usage_data}"
            )

    # --- Azure OpenAI の場合 ---
    if "gpt" in model_name.lower() or "o3" in model_name.lower():
        logger.info(f"'{model_name}' selected. Preparing content for Azure OpenAI API...")

        processed_contents_for_gpt = []
        for content in contents:
            processed_parts = []
            for part in content.parts:
                # PDFが含まれる場合はテキスト抽出を行う
                if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.mime_type == "application/pdf":
                    logger.info("PDF part detected. Extracting text for Azure OpenAI...")
                    text_part_from_pdf = _extract_text_from_pdf_part(part)
                    if text_part_from_pdf:
                        processed_parts.append(text_part_from_pdf)
                else:
                    processed_parts.append(part)
            if processed_parts:
                processed_contents_for_gpt.append(types.Content(role=content.role, parts=processed_parts))
        
        return _generate_with_azure_openai(
            model_name=model_name, contents=processed_contents_for_gpt, system_instruction=system_instruction,
            max_output_tokens=max_output_tokens, temperature=temperature, top_p=top_p, stream=stream,
            usage_log_blob_name=usage_log_blob_name,
            debug_log_on_parent_usage_aggregation=debug_parent_aggregation,
            debug_parent_log_blob_name=effective_parent_log_id,
            debug_feature_category=feature_category,
        )

    # --- Gemini の場合 ---
    client = get_gemini_client()
    if not client:
        error_message = "[エラー: Geminiクライアントの初期化に失敗しました。]"
        logger.error(error_message)
        if stream:
            def error_stream(): yield type('obj', (object,), {'text': error_message, 'image_data_url': None}); return error_stream()
        return error_message, None

    is_image_generation_model = "image" in model_name
    processed_contents = contents

    # 画像生成の場合、プロンプトの英語翻訳などを試行（既存ロジック）
    if is_image_generation_model:
        logger.info(f"Image generation model '{model_name}' detected. Preparing prompt...")
        
        is_edit_task = any(
            hasattr(part, 'inline_data') and part.inline_data and part.inline_data.mime_type.startswith("image/")
            for content in contents for part in content.parts
        )
        logger.info(f"Image task classified as: {'EDIT' if is_edit_task else 'NEW GENERATION'}")

        original_prompt_text = ""
        target_content_index = -1
        target_part_index = -1

        for i in range(len(contents) - 1, -1, -1):
            content = contents[i]
            if content.role == "user":
                for j, part in enumerate(content.parts):
                    if hasattr(part, 'text') and part.text is not None and part.text.strip():
                        original_prompt_text = part.text
                        target_content_index = i
                        target_part_index = j
                        break
            if original_prompt_text:
                break 

        if original_prompt_text:
            logger.info("Attempting to translate the prompt to English for better image generation quality.")
            try:
                if is_edit_task:
                    translation_system_prompt = (
                        "You are an expert translator specializing in image editing commands. "
                        "Translate the following Japanese instruction into a clear and direct English command for an advanced image editing AI. "
                        "The command should explicitly reference the provided base image(s). "
                        "Focus on the action to be performed on the image. "
                        "For example, 'この人物を海辺に移動させて' should become something like 'Move the person in this image to a beachside setting.' "
                        "Only output the translated English command."
                    )
                else:
                    translation_system_prompt = (
                        "You are a professional translator. "
                        "Translate the following Japanese text into a creative, detailed, and high-quality English prompt suitable for an advanced image generation AI. "
                        "Capture the original intent and enhance it with vivid and descriptive language. "
                        "Only output the translated English prompt, without any other text or explanation."
                    )
                
                translation_contents = [types.Content(role='user', parts=[types.Part(text=original_prompt_text)])]

                # 翻訳のために自分自身を再帰呼び出し（ログはスキップ）
                translated_text, _ = generate_with_gemini(
                    model_name=config.DEFAULT_MODEL_NAME,
                    contents=translation_contents,
                    system_instruction=translation_system_prompt,
                    max_output_tokens=8192,
                    temperature=0.2,
                    stream=False,
                    skip_cosmos_log=True,
                    parent_log_id=usage_log_blob_name or effective_parent_log_id
                )

                if translated_text and isinstance(translated_text, str) and not translated_text.strip().startswith("[APIエラー"):
                    logger.info(f"Original prompt: '{original_prompt_text}'")
                    logger.info(f"Translated prompt: '{translated_text.strip()}'")
                    
                    new_contents_list = []
                    for i, content in enumerate(contents):
                        new_parts = []
                        for j, part in enumerate(content.parts):
                            if i == target_content_index and j == target_part_index:
                                new_parts.append(types.Part(text=translated_text.strip()))
                            else:
                                text_val = getattr(part, 'text', None)
                                inline_data_val = getattr(part, 'inline_data', None)
                                file_data_val = getattr(part, 'file_data', None)

                                if text_val:
                                    new_parts.append(types.Part(text=text_val))
                                elif inline_data_val:
                                    new_parts.append(types.Part(inline_data=inline_data_val))
                                elif file_data_val:
                                    new_parts.append(types.Part(file_data=file_data_val))
                                else:
                                    logger.warning(f"Unknown part type encountered. Part dict: {part.__dict__ if hasattr(part, '__dict__') else 'N/A'}")
                                    new_parts.append(part)
                        
                        new_contents_list.append(types.Content(role=content.role, parts=new_parts))
                    
                    processed_contents = new_contents_list
                    
                else:
                    logger.warning("Translation failed or returned an empty result. Using the original Japanese prompt.")
            except Exception as e:
                logger.error(f"An error occurred during prompt translation: {e}", exc_info=True)
                logger.warning("Using the original Japanese prompt due to a translation error.")
        else:
            logger.info("No text prompt found to translate for image generation.")

        # --- 2. 画像生成モデル実行 ---
        logger.info(f"Image generation model '{model_name}' detected. Executing image generation flow.")
        
        config_args_image = {
            "max_output_tokens": 32768,
            "temperature": temperature,
            "response_modalities": ["TEXT", "IMAGE"],
            "safety_settings": [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ]
        }

        if aspect_ratio and aspect_ratio != 'auto':
            logger.info(f"Applying aspect ratio: {aspect_ratio}")
            config_args_image["image_config"] = types.ImageConfig(aspect_ratio=aspect_ratio)

        if system_instruction:
            config_args_image["system_instruction"] = [types.Part.from_text(text=system_instruction)]
        
        try:
            generate_content_config_image = types.GenerateContentConfig(**config_args_image)
            
            def _call_generate_stream():
                return client.models.generate_content_stream(
                    model=model_name,
                    contents=processed_contents,
                    config=generate_content_config_image
                )

            response_iterator = _retry_on_resource_exhausted(_call_generate_stream)

            def image_stream_wrapper(iterator):
                final_usage = None
                try:
                    for chunk in iterator:
                        # Usage取得
                        if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                            final_usage = chunk.usage_metadata

                        try:
                            if (chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts):
                                for part in chunk.candidates[0].content.parts:
                                    if part.inline_data and part.inline_data.data:
                                        # 画像データをBlob Storageにアップロードし、URLを返す
                                        mime_type = part.inline_data.mime_type
                                        ext = mime_type.split('/')[-1] if mime_type else 'png'
                                        blob_name = f"generated_images/{uuid.uuid4()}.{ext}"

                                        try:
                                            upload_bytes_to_blob(part.inline_data.data, blob_name)
                                            image_url = get_blob_sas_url(blob_name, expiration_minutes=43200) # 30日間
                                            
                                            if image_url:
                                                logger.info(f"Image generated and uploaded to Blob: {blob_name}")
                                                base64_data = base64.b64encode(part.inline_data.data).decode('utf-8')
                                                data_url = f"data:{mime_type};base64,{base64_data}"
                                                yield type('obj', (object,), {'text': '', 'image_data_url': data_url})
                                            else:
                                                raise Exception("Failed to generate SAS URL")
                                        except Exception as upload_error:
                                            logger.error(f"Failed to upload generated image to Blob: {upload_error}", exc_info=True)
                                            base64_data = base64.b64encode(part.inline_data.data).decode('utf-8')
                                            data_url = f"data:{mime_type};base64,{base64_data}"
                                            yield type('obj', (object,), {'text': '', 'image_data_url': data_url})

                                    elif hasattr(part, 'text') and part.text:
                                        yield type('obj', (object,), {'text': part.text, 'image_data_url': None})
                            elif hasattr(chunk, 'text') and chunk.text:
                                yield type('obj', (object,), {'text': chunk.text, 'image_data_url': None})
                        except Exception as e_chunk:
                            logger.error(f"Image stream chunk processing error: {e_chunk}")
                            continue
                except Exception as e_iter:
                    logger.error(f"Error during image stream iteration: {e_iter}")
                    error_message = f"[APIエラー: {type(e_iter).__name__} - {e_iter}]"
                    yield type('obj', (object,), {'text': error_message, 'image_data_url': None})
                finally:
                    # ★ ストリーム終了後にトークン使用量をログに更新
                    if usage_log_blob_name and final_usage:
                        usage_data = {
                            "prompt_tokens": final_usage.prompt_token_count,
                            "completion_tokens": final_usage.candidates_token_count,
                            "total_tokens": final_usage.total_token_count
                        }
                        _update_usage_and_debug_if_needed(usage_data, provider="gemini-image")
            
            return image_stream_wrapper(response_iterator)

        except Exception as e:
            logger.error(f"Gemini image generation API call failed: {e}", exc_info=True)
            error_message = f"[APIエラー: {type(e).__name__} - {e}]"
            def error_stream():
                yield type('obj', (object,), {'text': error_message, 'image_data_url': None})
            return error_stream()

    else:
        # --- 3. テキスト生成モデル（従来通り）の処理フロー ---
        logger.info(f"Text generation model '{model_name}' detected. Executing standard text generation flow.")
        
        config_args_text = {
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
            **({"top_p": top_p} if top_p is not None else {}),
            "safety_settings": [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ],
            #"thinking_config": types.ThinkingConfig(thinking_budget=-1),
        }
        if system_instruction:
            config_args_text["system_instruction"] = [types.Part.from_text(text=system_instruction)]

        tools_to_use = []
        if urls_for_context and len(urls_for_context) > 0:
            tools_to_use.append(types.Tool(url_context=types.UrlContext()))

        if use_grounding:
            tools_to_use.append(types.Tool(google_search=types.GoogleSearch()))

        if use_code_execution:
            tools_to_use.append(types.Tool(code_execution=types.ToolCodeExecution))

        if tools_to_use:
            config_args_text["tools"] = tools_to_use

        if generation_config_override:
            config_args_text.update(generation_config_override)

        try:
            generate_content_config_text = types.GenerateContentConfig(**config_args_text)

            if stream:
                response_iterator = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=generate_content_config_text
                )
                def text_stream_wrapper(iterator):
                    final_usage = None
                    try:
                        for chunk in iterator:
                            # Usage取得
                            if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                                final_usage = chunk.usage_metadata
                            yield chunk
                    except Exception as e_iter:
                        logger.error(f"Error during text stream iteration: {e_iter}")
                        yield type('obj', (object,), {'text': f"[APIエラー: {type(e_iter).__name__} - {e_iter}]"})
                    finally:
                         # ★ ストリーム終了後にトークン使用量をログに更新
                        if usage_log_blob_name and final_usage:
                            usage_data = {
                                "prompt_tokens": final_usage.prompt_token_count,
                                "completion_tokens": final_usage.candidates_token_count,
                                "total_tokens": final_usage.total_token_count
                            }
                            _update_usage_and_debug_if_needed(usage_data, provider="gemini-text")

                return text_stream_wrapper(response_iterator)
            else:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=generate_content_config_text
                )

                # ★ 非ストリーム時のログ更新
                if usage_log_blob_name and hasattr(response, 'usage_metadata') and response.usage_metadata:
                    usage_data = {
                        "prompt_tokens": response.usage_metadata.prompt_token_count,
                        "completion_tokens": response.usage_metadata.candidates_token_count,
                        "total_tokens": response.usage_metadata.total_token_count
                    }
                    _update_usage_and_debug_if_needed(usage_data, provider="gemini-text")

                if return_full_response:
                    return response, getattr(response, 'usage_metadata', None)
                full_text = response.text if hasattr(response, 'text') else ""
                return full_text.strip(), getattr(response, 'usage_metadata', None)

        except Exception as e:
            logger.error(f"Gemini text generation API call failed: {e}", exc_info=True)
            error_message = f"[APIエラー: {type(e).__name__} - {e}]"
            if stream:
                def error_stream(): yield type('obj', (object,), {'text': error_message})
                return error_stream()
            return error_message, None

# --- リトライ用のデコレータ的関数 ---
def _retry_on_resource_exhausted(func, max_retries=5, base_delay=2):
    """
    429 Resource Exhausted エラー発生時にリトライを行うラッパー。
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            error_str = str(e)
            # 429エラー または Resource exhausted を検出
            if "429" in error_str or "Resource exhausted" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if attempt < max_retries:
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Gemini API Resource Exhausted (429). Retrying in {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries})...")
                    time.sleep(sleep_time)
                    continue
            # それ以外のエラー、またはリトライ回数超過時はそのまま例外を投げる
            raise e

