import os
import sys
import time
import logging

# パスの調整（直接実行時）
_here = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.join(_here, '..', '..')
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

# azure_search.py の日付抽出関数を再利用
from smarteditorplus.utils.azure_search import (
    _extract_date_from_filename,
    _extract_date_from_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# --- 環境変数 ---
SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_SERVICE_ENDPOINT")
SEARCH_ADMIN_KEY = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME")

FETCH_BATCH_SIZE = 1000   # 1回の検索で取得するドキュメント数
UPLOAD_BATCH_SIZE = 500   # 1回のアップロードバッチサイズ
SLEEP_BETWEEN_BATCHES = 0.5  # バッチ間のスリープ(秒)


def _estimate_last_updated(original_filename: str, content_head: str) -> str | None:
    """
    ファイル名と本文冒頭 (Step1 → Step2) で last_updated を推定する。
    どちらも失敗した場合は None を返す（last_updated を更新しない）。
    """
    # Step1: ファイル名
    date_str = _extract_date_from_filename(original_filename)
    if date_str:
        return date_str

    # Step2: 本文冒頭
    if content_head:
        date_str = _extract_date_from_text(content_head[:500])
        if date_str:
            return date_str

    return None


def fetch_all_documents(search_client: SearchClient) -> list[dict]:
    """全ドキュメントを取得して返す（ページングなし・上限100万件）。"""
    logger.info("全ドキュメントを取得中...")
    all_docs = []

    # Azure AI Search の search() はページングされるため skip で巡回する
    skip = 0
    while True:
        results = list(search_client.search(
            search_text="*",
            select=["id", "original_filename", "content_for_llm"],
            top=FETCH_BATCH_SIZE,
            skip=skip,
        ))
        if not results:
            break
        all_docs.extend(results)
        logger.info(f"  取得済み: {len(all_docs)} 件")
        if len(results) < FETCH_BATCH_SIZE:
            break
        skip += FETCH_BATCH_SIZE
        time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(f"合計 {len(all_docs)} 件のドキュメントを取得しました。")
    return all_docs


def build_update_batch(docs: list[dict], registered_at_str: str) -> list[dict]:
    """
    取得したドキュメントから更新用バッチを生成する。
    キー(id) + 更新対象フィールドのみを含む最小構造にする。
    """
    updates = []
    for doc in docs:
        doc_id = doc.get("id")
        if not doc_id:
            continue
        original_filename = doc.get("original_filename") or ""
        content_head = doc.get("content_for_llm") or ""
        last_updated = _estimate_last_updated(original_filename, content_head)
        update = {"id": doc_id, "registered_at": registered_at_str}
        if last_updated is not None:
            update["last_updated"] = last_updated
        else:
            logger.warning(f"  last_updated 推定不可のためスキップ: id={doc_id}, file={original_filename}")
        updates.append(update)
    return updates


def run_update() -> None:
    """メイン処理。"""
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME]):
        logger.error(
            "環境変数が不足しています。"
            "AZURE_SEARCH_SERVICE_ENDPOINT / AZURE_SEARCH_ADMIN_KEY / AZURE_SEARCH_INDEX_NAME を確認してください。"
        )
        sys.exit(1)

    credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX_NAME,
        credential=credential,
    )

    # スクリプト実行時刻を registered_at に統一
    registered_at_str = datetime.now(timezone.utc).isoformat()
    logger.info(f"registered_at として使用する時刻: {registered_at_str}")

    # 1. 全ドキュメント取得
    all_docs = fetch_all_documents(search_client)
    if not all_docs:
        logger.info("対象ドキュメントが見つかりませんでした。処理を終了します。")
        return

    # 2. 更新データ生成
    update_list = build_update_batch(all_docs, registered_at_str)
    logger.info(f"{len(update_list)} 件の更新データを生成しました。")

    # 3. バッチ更新 (merge_or_upload)
    total_updated = 0
    failed_count = 0
    for i in range(0, len(update_list), UPLOAD_BATCH_SIZE):
        batch = update_list[i: i + UPLOAD_BATCH_SIZE]
        try:
            results = search_client.merge_or_upload_documents(documents=batch)
            succeeded = sum(1 for r in results if r.succeeded)
            failed = len(results) - succeeded
            total_updated += succeeded
            failed_count += failed
            logger.info(
                f"バッチ {i // UPLOAD_BATCH_SIZE + 1}: "
                f"成功 {succeeded} 件 / 失敗 {failed} 件 (累計: {total_updated} 件)"
            )
            if failed:
                for r in results:
                    if not r.succeeded:
                        logger.warning(f"  更新失敗 id={r.key}, status={r.status_code}, error={r.error_message}")
        except Exception as e:
            logger.error(f"バッチ {i // UPLOAD_BATCH_SIZE + 1} の更新中にエラー: {e}", exc_info=True)
            failed_count += len(batch)
        time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(
        f"\n=== 完了 ===\n"
        f"  成功: {total_updated} 件\n"
        f"  失敗: {failed_count} 件"
    )


if __name__ == "__main__":
    run_update()
