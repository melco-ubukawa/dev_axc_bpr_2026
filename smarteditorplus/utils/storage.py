# utils/storage.py (修正版)

import os
import json
import logging
import base64
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, unquote
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.core.exceptions import HttpResponseError

try:
    # SDK差分吸収: 利用可能ならETag条件付き更新に使用
    from azure.storage.blob import MatchConditions
except Exception:  # pragma: no cover
    MatchConditions = None
from google.cloud import storage
import config

# --- ロガー設定 ---
logger = logging.getLogger(__name__)


# --- Google Cloud Storage メインプロジェクト用クライアント（シングルトン） ---
_main_gcs_client: storage.Client | None = None


def _get_google_cloud_name_for_log() -> str | None:
    """現在のリクエストコンテキストから google_cloud_name を取得してログ用に返す。

    - Flask のリクエストコンテキスト内であれば g.google_cloud_name を参照
    - それ以外の場合は config.GOOGLE_CLOUD_NAME をフォールバックとして返す
    """
    try:
        from flask import has_request_context, g as flask_g  # ローカルインポートで循環参照を回避

        if has_request_context():
            name = getattr(flask_g, "google_cloud_name", None)
            if name:
                return str(name)
    except Exception:
        # ログ用の付加情報取得なので、失敗しても処理は継続する
        pass

    try:
        return getattr(config, "GOOGLE_CLOUD_NAME", None)
    except Exception:
        return None


def _format_gcs_log_message(message: str) -> str:
    """GCS関連ログメッセージに google_cloud_name 情報を付与して返す。"""
    name = _get_google_cloud_name_for_log()
    if name:
        return f"[google_cloud_name={name}] {message}"
    return message


def get_main_gcs_client() -> storage.Client | None:
    """メインプロジェクト（GOOGLE_CLOUD_PROJECT）向けの GCS クライアントを返す。

    プロンプト取得・システムプロンプト取得/保存など、メインプロジェクト固定で
    利用したい処理はこのクライアントを経由させる。
    """
    global _main_gcs_client

    if _main_gcs_client is not None:
        return _main_gcs_client

    project_id = getattr(config, "GOOGLE_CLOUD_PROJECT", None) or getattr(config, "DEFAULT_GEMINI_PROJECT", None)
    try:
        _main_gcs_client = storage.Client(project=project_id) if project_id else storage.Client()
        logger.info(f"Initialized main GCS client for project: {project_id}")
    except Exception as e:
        logger.error(f"Failed to initialize main GCS client (project={project_id}): {e}", exc_info=True)
        return None

    return _main_gcs_client

# --- Azure Blob Storage 定数 ---
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
AZURE_STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "tempstorage")
AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME","activity-logs")
AZURE_STORAGE_CHAT_SESSIONS_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CHAT_SESSIONS_CONTAINER_NAME", "chat-sessions")
AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME", "pptx-history")
AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME", "chat-history-data")
AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME", "design-projects")


# --- Azure Blob Storage クライアント初期化 (シングルトン) ---
blob_service_client = None
_checked_containers: set[str] = set()


def _ensure_container_exists(client: BlobServiceClient, container_name: str) -> None:
    """Best-effort container existence check/creation with small in-process cache."""
    if not container_name:
        return

    if container_name in _checked_containers:
        return

    try:
        container_client = client.get_container_client(container_name)
        if not container_client.exists():
            container_client.create_container()
        _checked_containers.add(container_name)
    except Exception as e:
        # 実行環境/権限によっては作成できないため、ログだけ残して処理継続。
        logger.warning(f"Container check/creation failed for '{container_name}': {e}")


def get_blob_service_client():
    """Azure Blob Storageクライアントをシングルトンとして取得・初期化する。"""
    global blob_service_client
    if blob_service_client is None:
        if not AZURE_STORAGE_CONNECTION_STRING:
            logger.error("Azure Blob Service Client could not be initialized: AZURE_STORAGE_CONNECTION_STRING is not set.")
            return None
        try:
            # セキュリティ対応: connection_verify=False を削除し、SSL証明書検証を有効化（デフォルト）
            blob_service_client = BlobServiceClient.from_connection_string(
                conn_str=AZURE_STORAGE_CONNECTION_STRING
            )
            logger.info("Azure Blob Service Client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Azure Blob Service Client: {e}", exc_info=True)
            return None
    return blob_service_client

# --- Azure Blob Storage 操作ヘルパー関数 ---
def upload_bytes_to_blob(data: bytes, blob_name: str, *, container_name: str | None = None) -> str | None:
    client = get_blob_service_client()
    if not client:
        logger.error(f"Blobアップロード失敗: Azure Blob Service Clientが利用できません。 (Blob: {blob_name})")
        return None

    container = container_name or AZURE_STORAGE_CONTAINER_NAME
    try:
        _ensure_container_exists(client, container)
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(data, overwrite=True)
        logger.info(f"Uploaded data to blob: {container}/{blob_name}")
        return blob_client.url
    except Exception as e:
        logger.error(f"Failed to upload to blob {container}/{blob_name}: {e}", exc_info=True)
        return None

def upload_stream_to_blob(stream, blob_name: str, *, container_name: str | None = None) -> str | None:
    """
    ファイルストリーム（file-like オブジェクト）を全量メモリに読み込まずに
    Azure Blob Storage へストリームアップロードする。
    大容量ファイル（動画等）のメモリ節約に使用する。
    成功時は blob URL を返す。失敗時は None を返す。
    """
    client = get_blob_service_client()
    if not client:
        logger.error(f"Blobアップロード失敗: Azure Blob Service Clientが利用できません。 (Blob: {blob_name})")
        return None

    container = container_name or AZURE_STORAGE_CONTAINER_NAME
    try:
        _ensure_container_exists(client, container)
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        stream.seek(0)
        blob_client.upload_blob(stream, overwrite=True)
        logger.info(f"Streamed upload to blob: {container}/{blob_name}")
        return blob_client.url
    except Exception as e:
        logger.error(f"Failed to stream-upload to blob {container}/{blob_name}: {e}", exc_info=True)
        return None


def download_blob_to_bytes(blob_name: str, *, container_name: str | None = None) -> bytes | None:
    client = get_blob_service_client()
    if not client: return None

    container = container_name or AZURE_STORAGE_CONTAINER_NAME
    try:
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        if blob_client.exists():
            return blob_client.download_blob().readall()
        else:
            logger.warning(f"Blob not found: {container}/{blob_name}")
            return None
    except Exception as e:
        logger.error(f"Failed to download from blob {container}/{blob_name}: {e}", exc_info=True)
        return None

def delete_blob(blob_name: str):
    client = get_blob_service_client()
    if not client: return
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CONTAINER_NAME, blob=blob_name)
        if blob_client.exists():
            blob_client.delete_blob()
            logger.info(f"Deleted blob: {blob_name}")
    except Exception as e:
        if "BlobNotFound" not in str(e):
            logger.error(f"Failed to delete blob {blob_name}: {e}", exc_info=True)

def get_blob_sas_url(blob_name: str, expiration_minutes: int = 60) -> str | None:
    client = get_blob_service_client()
    if not client or not client.credential or not hasattr(client.credential, 'account_key'):
        logger.error("Blob client or account key not available for SAS URL generation.")
        return None
    try:
        sas_token = generate_blob_sas(
            account_name=client.account_name,
            container_name=AZURE_STORAGE_CONTAINER_NAME,
            blob_name=blob_name,
            account_key=client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
        )
        blob_url = f"https://{client.account_name}.blob.core.windows.net/{AZURE_STORAGE_CONTAINER_NAME}/{blob_name}"
        return f"{blob_url}?{sas_token}"
    except Exception as e:
        logger.error(f"Failed to generate SAS URL for blob {blob_name}: {e}", exc_info=True)
        return None

def upload_session_to_blob(user_id: str, session_id: str, session_data: dict) -> bool:
    """完全なセッションデータをJSONとしてチャットセッション用コンテナにアップロードする"""
    client = get_blob_service_client()
    if not client:
        return False
    
    blob_name = f"{user_id}/{session_id}.json"
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_SESSIONS_CONTAINER_NAME, blob=blob_name)
        # 人間が読めるようにインデント付きで保存
        json_bytes = json.dumps(session_data, indent=2, ensure_ascii=False).encode('utf-8')
        blob_client.upload_blob(json_bytes, overwrite=True)
        logger.info(f"Session data for '{session_id}' uploaded to blob: {blob_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload session blob '{blob_name}': {e}", exc_info=True)
        return False

def download_session_from_blob(user_id: str, session_id: str) -> dict | None:
    """チャットセッション用コンテナからセッションデータをダウンロードし、辞書として返す"""
    client = get_blob_service_client()
    if not client:
        return None
        
    blob_name = f"{user_id}/{session_id}.json"
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_SESSIONS_CONTAINER_NAME, blob=blob_name)
        if blob_client.exists():
            downloader = blob_client.download_blob()
            json_bytes = downloader.readall()
            session_data = json.loads(json_bytes.decode('utf-8'))
            logger.info(f"Session data for '{session_id}' downloaded from blob: {blob_name}")
            return session_data
        else:
            logger.warning(f"Session blob not found: {blob_name}")
            return None
    except Exception as e:
        logger.error(f"Failed to download or parse session blob '{blob_name}': {e}", exc_info=True)
        return None

def delete_session_blob(user_id: str, session_id: str) -> bool:
    """チャットセッション用コンテナから指定されたセッションのBlobを削除する"""
    client = get_blob_service_client()
    if not client:
        return False
        
    blob_name = f"{user_id}/{session_id}.json"
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_SESSIONS_CONTAINER_NAME, blob=blob_name)
        if blob_client.exists():
            blob_client.delete_blob()
            logger.info(f"Session blob deleted: {blob_name}")
        else:
            logger.info(f"Session blob to be deleted was not found: {blob_name}")
        return True
    except Exception as e:
        if "BlobNotFound" not in str(e):
            logger.error(f"Failed to delete session blob '{blob_name}': {e}", exc_info=True)
        return False


# --- Google Cloud Storage (GCS) 操作ヘルパー関数 ---
def _upload_to_gcs(file_stream, destination_blob_name: str, bucket_name: str) -> str | None:
    """ファイルストリームをGCSにアップロードし、GCS URIを返す"""
    if not bucket_name:
        logger.error("GCSバケット名が環境変数に設定されていません。")
        return None
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、ファイルをアップロードできません。")
            return None
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        file_stream.seek(0)
        blob.upload_from_file(file_stream)
        
        gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"

        logger.info(_format_gcs_log_message(f"ファイル '{destination_blob_name}' をGCS ({gcs_uri}) にアップロードしました。"))
        return gcs_uri
        
    except Exception as e:
        logger.error(f"GCSへのファイル '{destination_blob_name}' アップロード中にエラー: {e}", exc_info=True)
        return None

def _sanitize_gcs_object_name_part(name_part: str, is_folder: bool = False) -> str | None:
    """
    GCSのオブジェクト名の一部（フォルダ名またはファイル名）として安全な文字列にする。
    不正な場合はNoneを返す。
    """
    if not name_part or not isinstance(name_part, str):
        return None
    
    name_part = name_part.strip()
    if not name_part:
        return None

    if '/' in name_part or '\\' in name_part or '..' in name_part:
        logger.warning(f"禁止文字 (パス区切り文字など) を含む名前が指定されました: {name_part}")
        return None
    
    MAX_NAME_LENGTH = 100
    if len(name_part) > MAX_NAME_LENGTH:
        logger.warning(f"名前が長すぎます (最大{MAX_NAME_LENGTH}文字): {name_part}")
        return None
        
    return name_part

def _list_gcs_folders_in_prefix(bucket_name: str, prefix: str) -> list[str] | None:
    if not bucket_name:
        logger.error("GCSバケット名が環境変数に設定されていません。")
        return None
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、フォルダ一覧を取得できません。")
            return None
        logger.debug("メインGCSクライアントの初期化に成功。")
        blobs = storage_client.list_blobs(bucket_name, prefix=prefix, delimiter='/')
        folder_names = []
        # blobs.pages は非推奨になったため、直接イテレートする
        for page in blobs.pages:
            if hasattr(page, 'prefixes') and page.prefixes:
                for folder_prefix in page.prefixes:
                    folder_name = folder_prefix.replace(prefix, "", 1).strip('/')
                    if folder_name:
                        folder_names.append(folder_name)
        
        # target_categories_str = os.environ.get("TARGET_CHAT_PROMPT_CATEGORIES")
        # if target_categories_str:
        #     target_categories_list = [cat.strip() for cat in target_categories_str.split(',') if cat.strip()]
        #     if target_categories_list:
        #         filtered_folder_names = [name for name in folder_names if name in target_categories_list]
        #         return sorted(list(set(filtered_folder_names)))
        return sorted(list(set(folder_names)))
    except Exception as e:
        logger.error(f"GCSプレフィックス '{prefix}' からのフォルダ一覧取得中にエラー: {e}", exc_info=True)
        return None

def _list_gcs_files_in_prefix(bucket_name: str, prefix: str, extension_filter: str = ".txt") -> list[str] | None:
    if not bucket_name:
        logger.error("GCSバケット名が設定されていません。")
        return None
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、ファイル一覧を取得できません。")
            return None
        logger.debug("メインGCSクライアントの初期化に成功。")
        blobs = storage_client.list_blobs(bucket_name, prefix=prefix)
        filenames = []
        for blob in blobs:
            if blob.name != prefix and (not extension_filter or blob.name.lower().endswith(extension_filter)):
                filename_with_ext = blob.name.replace(prefix, "", 1)
                if '/' not in filename_with_ext:
                    # ★★★ 修正: ここでは拡張子を除去しないように変更 ★★★
                    # 呼び出し元で拡張子が必要なケースと不要なケースがあるため、
                    # この関数は拡張子付きのファイル名を返すように統一し、呼び出し元で処理する方が堅牢。
                    # ただし、元のapp.pyのロジックを維持するため、ここでは拡張子を除去したままにします。
                    filename_without_ext = os.path.splitext(filename_with_ext)[0]
                    if filename_without_ext:
                        filenames.append(filename_without_ext)
        return sorted(list(set(filenames)))
    except Exception as e:
        logger.error(f"GCSプレフィックス '{prefix}' からのファイル一覧取得中にエラー: {e}", exc_info=True)
        return None

def _load_gcs_prompt_content(bucket_name: str, file_path: str) -> tuple[str | None, dict]:
    """
    【修正】GCSからプロンプトのテキスト内容とメタデータ全体を読み込む。
    戻り値を (content, metadata_dict) に変更。
    """
    if not bucket_name:
        logger.error("GCSバケット名が設定されていません。")
        return None, {}
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、プロンプトを読み込めません。")
            return None, {}
        logger.debug("メインGCSクライアントの初期化に成功。")
        bucket_obj = storage_client.bucket(bucket_name)
        blob = bucket_obj.blob(file_path)
        
        if not blob.exists():
            logger.warning(f"GCSプロンプトファイルが見つかりません: '{file_path}'")
            return None, {}

        # メタデータをリロードして最新の状態を取得
        blob.reload()
        
        prompt_content = blob.download_as_text(encoding="utf-8")
        
        # blob.metadata はNoneの場合があるので、空の辞書をフォールバックとして用意
        metadata = blob.metadata or {}
        
        logger.info(f"GCSファイル '{file_path}' からプロンプト内容とメタデータ({metadata})を読み込みました。")
        # 戻り値をテキストとメタデータ辞書全体に変更
        return prompt_content, metadata

    except Exception as e:
        logger.error(f"GCSファイル '{file_path}' の読み込み中にエラー: {e}", exc_info=True)
        return None, {}

def _upload_text_to_gcs(bucket_name: str, destination_blob_name: str, text_content: str, metadata: dict | None = None) -> str | None:
    if not bucket_name:
        logger.error("GCSバケット名が設定されていません。")
        return None
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、テキストをアップロードできません。")
            return None
        logger.debug("メインGCSクライアントの初期化に成功。")
        bucket_obj = storage_client.bucket(bucket_name)
        blob = bucket_obj.blob(destination_blob_name)

        if metadata:
            blob.metadata = metadata

        blob.upload_from_string(text_content, content_type='text/plain; charset=utf-8')
        gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
        logger.info(_format_gcs_log_message(f"テキストをGCS ({gcs_uri}) にアップロードしました。"))
        return gcs_uri
    except Exception as e:
        logger.error(f"GCSへのテキスト '{destination_blob_name}' アップロード中にエラー: {e}", exc_info=True)
        return None

def _gcs_blob_exists(bucket_name: str, blob_name: str) -> bool:
    if not bucket_name:
        return False
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、存在確認を行えません。")
            return False
        logger.debug("メインGCSクライアントの初期化に成功。")
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        return blob.exists()
    except Exception as e:
        logger.error(_format_gcs_log_message(f"GCSファイル gs://{bucket_name}/{blob_name} の存在確認中にエラー: {e}"), exc_info=True)
        return False
    
def download_gcs_blob_to_bytes(gcs_uri: str) -> bytes | None:
    """GCS URIからファイルをバイトデータとしてダウンロードする"""
    try:
        if not gcs_uri.startswith("gs://"):
            logger.error(_format_gcs_log_message(f"無効なGCS URIです: {gcs_uri}"))
            return None

        # メインGCSクライアントを初期化
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、ファイルをダウンロードできません。")
            return None
        
        # GCS URIからバケット名とblob名を正しくパース
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            logger.warning(_format_gcs_log_message(f"GCSファイルが見つかりません: {gcs_uri}"))
            return None
        
        logger.info(_format_gcs_log_message(f"GCSからファイルをダウンロードしています: {gcs_uri}"))
        return blob.download_as_bytes()

    except Exception as e:
        logger.error(_format_gcs_log_message(f"GCSファイルのダウンロードに失敗しました ({gcs_uri}): {e}"), exc_info=True)
        return None

def _delete_gcs_blob(bucket_name: str, blob_name: str) -> bool:
    """
    Google Cloud Storageから指定されたblobを削除する。
    """
    if not bucket_name:
        logger.warning("GCSバケット名が未設定のため、ファイル削除をスキップします。")
        return False
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、ファイル削除を行えません。")
            return False
        logger.debug("メインGCSクライアントの初期化に成功。")
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        if blob.exists():
            blob.delete()
            logger.info(_format_gcs_log_message(f"GCSファイル gs://{bucket_name}/{blob_name} を正常に削除しました。"))
            return True
        else:
            logger.info(_format_gcs_log_message(f"削除対象のGCSファイル gs://{bucket_name}/{blob_name} は存在しませんでした。"))
            # 存在しない場合も、目的は達成されているのでTrueを返す
            return True
    except Exception as e:
        logger.error(_format_gcs_log_message(f"GCSファイル gs://{bucket_name}/{blob_name} の削除中にエラーが発生しました: {e}"), exc_info=True)
        return False
    
def get_gcs_signed_url(blob_name: str, bucket_name: str, expiration_minutes: int = 60) -> str | None:
    """GCSオブジェクトの署名付きURLを生成する"""
    if not bucket_name:
        logger.error("GCS bucket name is not provided for signed URL generation.")
        return None
    try:
        storage_client = get_main_gcs_client()
        if not storage_client:
            logger.error("メインGCSクライアントの初期化に失敗したため、署名付きURLを生成できません。")
            return None
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            logger.warning(f"Blob {blob_name} does not exist in bucket {bucket_name}.")
            return None

        expiration_time = timedelta(minutes=expiration_minutes)
        
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=expiration_time,
            method="GET"
        )
        return signed_url
    except Exception as e:
        logger.error(f"Failed to generate signed URL for GCS blob {blob_name}: {e}", exc_info=True)
        return None
    
def _delete_from_gcs(gcs_uri: str, bucket_name: str | None = None) -> bool:
    """
    【改修版】GCS URIからファイルを削除する。
    URIが渡された場合はそれを優先して解析し、ない場合はbucket_nameとblob_name(gcs_uri引数に入る)を使用する。
    """
    try:
        # URI形式でない場合、従来の引数（bucket_name, blob_name）として扱うための後方互換処理
        if gcs_uri and not gcs_uri.startswith("gs://"):
            blob_name_from_arg = gcs_uri
            if not bucket_name:
                logger.error(f"GCS URI形式でなく、bucket_nameも指定されていません。削除できません: {blob_name_from_arg}")
                return False
            
            # 従来の引数で削除処理を実行
            return _delete_gcs_blob(bucket_name, blob_name_from_arg)

        # URI形式の場合
        if not gcs_uri:
            logger.warning("空のGCS URIが指定されたため、削除をスキップします。")
            return False

        # URIからバケット名とblob名をパース
        parsed_bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        
        return _delete_gcs_blob(parsed_bucket_name, blob_name)

    except Exception as e:
        logger.error(_format_gcs_log_message(f"GCSリソース '{gcs_uri}' の削除準備中にエラーが発生しました: {e}"), exc_info=True)
        return False

def upload_pptx_history_to_blob(user_id: str, history_id: str, history_data: dict) -> bool:
    """
    生成されたPPTXスライドの履歴データ（JSON）をBlob Storageに保存する。
    一覧表示用に主要項目をメタデータとして付与する。
    """
    client = get_blob_service_client()
    if not client:
        return False
    
    blob_name = f"{user_id}/{history_id}.json"
    try:
        # コンテナが存在しない場合は作成（初回のみ）
        try:
            client.create_container(AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME)
        except Exception:
            pass # 既に存在する場合は無視

        blob_client = client.get_blob_client(container=AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME, blob=blob_name)
        
        json_bytes = json.dumps(history_data, indent=2, ensure_ascii=False).encode('utf-8')
        
        # 一覧取得時に中身をDLしなくて済むよう、メタデータを設定
        # 日本語が含まれる可能性があるためBase64エンコードする
        objective = history_data.get('objective', '')[:100] # 長すぎる場合はカット
        design_concept = history_data.get('design_concept', '')
        timestamp = history_data.get('timestamp', '')
        
        metadata = {
            "objective_b64": base64.b64encode(objective.encode('utf-8')).decode('utf-8'),
            "design_concept": design_concept,
            "timestamp": timestamp
        }

        blob_client.upload_blob(json_bytes, overwrite=True, metadata=metadata)
        logger.info(f"PPTX history '{history_id}' uploaded to blob: {blob_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload PPTX history blob '{blob_name}': {e}", exc_info=True)
        return False

def list_pptx_histories_from_blob(user_id: str) -> list:
    """
    ユーザーのPPTX生成履歴一覧を取得する。
    Blobのメタデータから概要情報を読み取る。
    """
    client = get_blob_service_client()
    if not client:
        return []
    
    prefix = f"{user_id}/"
    histories = []
    
    try:
        container_client = client.get_container_client(AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME)
        if not container_client.exists():
            return []

        # メタデータを含めてBlobをリスト
        blobs = container_client.list_blobs(name_starts_with=prefix, include=['metadata'])
        
        for blob in blobs:
            try:
                # ファイル名からIDを取得
                history_id = os.path.splitext(os.path.basename(blob.name))[0]
                metadata = blob.metadata or {}
                
                objective = ""
                if "objective_b64" in metadata:
                    try:
                        objective = base64.b64decode(metadata["objective_b64"]).decode('utf-8')
                    except Exception:
                        objective = "(decoding error)"
                
                # timestampがメタデータにない場合はBlob作成日時を使用
                ts = metadata.get("timestamp")
                if not ts and blob.creation_time:
                    ts = blob.creation_time.isoformat()

                histories.append({
                    "id": history_id,
                    "timestamp": ts,
                    "objective": objective,
                    "design_concept": metadata.get("design_concept", ""),
                    "blob_name": blob.name
                })
            except Exception as e:
                logger.warning(f"Error parsing blob metadata for {blob.name}: {e}")
                continue
                
        # 日付の降順でソート
        histories.sort(key=lambda x: x['timestamp'], reverse=True)
        return histories

    except Exception as e:
        logger.error(f"Failed to list PPTX histories for user '{user_id}': {e}", exc_info=True)
        return []

def get_pptx_history_from_blob(user_id: str, history_id: str) -> dict | None:
    """
    指定されたIDのPPTX生成履歴詳細（JSONデータ全体）を取得する。
    """
    client = get_blob_service_client()
    if not client:
        return None
        
    blob_name = f"{user_id}/{history_id}.json"
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME, blob=blob_name)
        if blob_client.exists():
            downloader = blob_client.download_blob()
            json_bytes = downloader.readall()
            history_data = json.loads(json_bytes.decode('utf-8'))
            logger.info(f"PPTX history '{history_id}' downloaded from blob: {blob_name}")
            return history_data
        else:
            logger.warning(f"PPTX history blob not found: {blob_name}")
            return None
    except Exception as e:
        logger.error(f"Failed to download PPTX history blob '{blob_name}': {e}", exc_info=True)
        return None

def delete_pptx_history_blob(user_id: str, history_id: str) -> bool:
    """
    指定されたIDのPPTX生成履歴を削除する。
    """
    client = get_blob_service_client()
    if not client:
        return False
        
    blob_name = f"{user_id}/{history_id}.json"
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_PPTX_HISTORY_CONTAINER_NAME, blob=blob_name)
        if blob_client.exists():
            blob_client.delete_blob()
            logger.info(f"PPTX history blob deleted: {blob_name}")
            return True
        else:
            logger.info(f"PPTX history blob to be deleted was not found: {blob_name}")
            return False
    except Exception as e:
        logger.error(f"Failed to delete PPTX history blob '{blob_name}': {e}", exc_info=True)
        return False

def save_chat_history(user_id: str, session_id: str, messages_list: list) -> str | None:
    """
    チャットメッセージのリストをJSONとしてBlob Storageに保存する。
    戻り値: 保存されたBlobのパス (コンテナ内の相対パス)。失敗時はNone。
    """
    client = get_blob_service_client()
    if not client:
        return None
    
    # コンテナが存在することを確認（初回用）
    try:
        container_client = client.get_container_client(AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container()
    except Exception as e:
        logger.warning(f"Container check/creation failed for '{AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME}': {e}")
        # クライアント権限によってはcreateが失敗しても、コンテナが既に存在すれば書き込みは成功する場合があるため続行

    blob_path = f"{user_id}/{session_id}.json"
    
    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME, blob=blob_path)
        
        # JSON配列として保存 (日本語文字列をエスケープしない)
        json_bytes = json.dumps(messages_list, indent=2, ensure_ascii=False).encode('utf-8')
        
        blob_client.upload_blob(json_bytes, overwrite=True)
        logger.info(f"Chat history for session '{session_id}' saved to blob: {blob_path}")
        return blob_path
    except Exception as e:
        logger.error(f"Failed to save chat history to blob '{blob_path}': {e}", exc_info=True)
        return None

def load_chat_history(blob_path: str) -> list | None:
    """
    指定されたBlobパスからチャット履歴(JSON配列)を読み込む。
    """
    client = get_blob_service_client()
    if not client:
        return None

    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME, blob=blob_path)
        
        if not blob_client.exists():
            logger.warning(f"Chat history blob not found: {blob_path}")
            return None
            
        downloader = blob_client.download_blob()
        json_bytes = downloader.readall()
        messages_list = json.loads(json_bytes.decode('utf-8'))
        return messages_list
    except Exception as e:
        logger.error(f"Failed to load chat history from blob '{blob_path}': {e}", exc_info=True)
        return None

def delete_chat_history(blob_path: str) -> bool:
    """
    指定されたBlobパスのチャット履歴を削除する。
    """
    client = get_blob_service_client()
    if not client:
        return False

    try:
        blob_client = client.get_blob_client(container=AZURE_STORAGE_CHAT_HISTORY_CONTAINER_NAME, blob=blob_path)
        if blob_client.exists():
            blob_client.delete_blob()
            logger.info(f"Chat history blob deleted: {blob_path}")
            return True
        else:
            logger.info(f"Chat history blob to be deleted was not found: {blob_path}")
            # 既に存在しない場合も削除完了とみなす
            return True
    except Exception as e:
        logger.error(f"Failed to delete chat history blob '{blob_path}': {e}", exc_info=True)
        return False

def update_blob_json(blob_name: str, updates: dict, *, container_name: str | None = None) -> bool:
    """
    指定されたBlob(JSON)をダウンロードし、updatesの内容をマージして再アップロードする。
    """
    client = get_blob_service_client()
    if not client:
        return False
    
    try:
        container = container_name or AZURE_STORAGE_CONTAINER_NAME
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        
        if not blob_client.exists():
            logger.warning(f"Log blob to update not found: {container}/{blob_name}")
            return False
            
        # 1. ダウンロード
        download_stream = blob_client.download_blob()
        data = json.loads(download_stream.readall())
        
        # 2. データのマージ (トップレベルのキーを更新/追加)
        data.update(updates)
        
        # 3. 再アップロード (上書き)
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        blob_client.upload_blob(json_bytes, overwrite=True)
        
        logger.info(f"Updated JSON blob: {container}/{blob_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to update JSON blob {container}/{blob_name}: {e}", exc_info=True)
        return False


def _is_precondition_failed_error(e: Exception) -> bool:
    """ETag競合(Precondition Failed/ConditionNotMet)っぽい例外かどうかを雑に判定する。"""
    try:
        if isinstance(e, HttpResponseError):
            # azure.core.exceptions.HttpResponseError
            status = getattr(e, "status_code", None)
            if status == 412:
                return True
    except Exception:
        pass

    msg = str(e)
    return any(x in msg for x in ["412", "PreconditionFailed", "ConditionNotMet", "ResourceModified"]) 


def update_blob_json_atomic(
    blob_name: str,
    update_func,
    *,
    container_name: str | None = None,
    max_retries: int = 5,
    base_delay_sec: float = 0.2,
) -> bool:
    """Blob上のJSONを原子的に更新する（可能ならETagで競合検知してリトライ）。

    - update_func: dict を受け取り、更新後の dict を返す関数
    """
    client = get_blob_service_client()
    if not client:
        return False

    container = container_name or AZURE_STORAGE_CONTAINER_NAME

    try:
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        if not blob_client.exists():
            logger.warning(f"Log blob to update not found: {container}/{blob_name}")
            return False
    except Exception as e:
        logger.error(f"Failed to access JSON blob {container}/{blob_name}: {e}", exc_info=True)
        return False

    for attempt in range(max_retries + 1):
        try:
            props = blob_client.get_blob_properties()
            etag = getattr(props, "etag", None)

            raw = blob_client.download_blob().readall()
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
            else:
                data = {}

            if not isinstance(data, dict):
                data = {}

            updated = update_func(data)
            if not isinstance(updated, dict):
                logger.error(f"update_func must return dict for {blob_name}")
                return False

            json_bytes = json.dumps(updated, indent=2, ensure_ascii=False).encode("utf-8")

            # 可能ならETag条件付きで上書き（競合時は412になる）
            if etag and MatchConditions is not None:
                blob_client.upload_blob(
                    json_bytes,
                    overwrite=True,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
            elif etag:
                # 古いSDK互換: if_match が通る場合がある
                try:
                    blob_client.upload_blob(json_bytes, overwrite=True, if_match=etag)
                except TypeError:
                    blob_client.upload_blob(json_bytes, overwrite=True)
            else:
                blob_client.upload_blob(json_bytes, overwrite=True)

            logger.info(f"Updated JSON blob (atomic): {container}/{blob_name}")
            return True

        except Exception as e:
            if _is_precondition_failed_error(e) and attempt < max_retries:
                sleep_time = base_delay_sec * (2 ** attempt)
                logger.warning(
                    f"JSON blob update conflict; retrying in {sleep_time:.2f}s "
                    f"(attempt {attempt + 1}/{max_retries}) blob={container}/{blob_name}"
                )
                import time

                time.sleep(sleep_time)
                continue

            logger.error(f"Failed to update JSON blob (atomic) {container}/{blob_name}: {e}", exc_info=True)
            return False

    return False


def add_usage_to_blob_json(
    blob_name: str,
    usage_delta: dict,
    *,
    extra_updates: dict | None = None,
    container_name: str | None = None,
    max_retries: int = 5,
) -> bool:
    """Blob(JSON)の usage を加算更新する。

    - usage_delta: {prompt_tokens, completion_tokens, total_tokens} を想定（存在するキーのみ加算）
    - extra_updates: usage以外のトップレベルキーを上書きする場合に使用
    """
    if not blob_name:
        return False

    def _merge(data: dict) -> dict:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        for key, val in (usage_delta or {}).items():
            if val is None:
                continue
            try:
                inc = int(val)
            except Exception:
                continue
            try:
                cur = int(usage.get(key) or 0)
            except Exception:
                cur = 0
            usage[key] = cur + inc

        data["usage"] = usage

        if extra_updates:
            data.update(extra_updates)

        return data

    return update_blob_json_atomic(
        blob_name,
        _merge,
        container_name=container_name,
        max_retries=max_retries,
    )

def upload_json_to_blob(data: dict, blob_name: str, container_name: str = AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME) -> bool:
    """
    辞書データをJSONとしてBlob Storageにアップロードする。
    """
    client = get_blob_service_client()
    if not client:
        return False
    
    try:
        # コンテナが存在しない場合は作成（初回用）
        try:
            container_client = client.get_container_client(container_name)
            if not container_client.exists():
                container_client.create_container()
        except Exception as e:
            logger.warning(f"Container check/creation failed for '{container_name}': {e}")

        blob_client = client.get_blob_client(container=container_name, blob=blob_name)
        
        # JSONシリアライズ
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        
        blob_client.upload_blob(json_bytes, overwrite=True)
        logger.info(f"JSON data uploaded to blob: {container_name}/{blob_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload JSON to blob '{blob_name}': {e}", exc_info=True)
        return False

def download_json_from_blob(blob_name: str, container_name: str = AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME) -> dict | None:
    """
    Blob StorageからJSONデータをダウンロードして辞書として返す。
    """
    client = get_blob_service_client()
    if not client:
        return None
        
    try:
        blob_client = client.get_blob_client(container=container_name, blob=blob_name)
        
        if not blob_client.exists():
            logger.warning(f"JSON blob not found: {container_name}/{blob_name}")
            return None
            
        downloader = blob_client.download_blob()
        json_bytes = downloader.readall()
        data = json.loads(json_bytes.decode('utf-8'))
        return data
    except Exception as e:
        logger.error(f"Failed to download JSON from blob '{blob_name}': {e}", exc_info=True)
        return None

def download_bytes_from_blob_url(blob_url):
    """
    BlobのURLからBlob名を特定し、内部クライアントを使ってバイトデータをダウンロードする。
    URLの形式から自動的にエミュレータ(Azurite)か本番かを判定する。
    """
    try:
        # URLをパース
        parsed = urlparse(blob_url)
        path = unquote(parsed.path) # /devstoreaccount1/container/path/to/blob
        
        parts = path.strip('/').split('/')
        
        container_name = ""
        blob_name = ""
        
        # ▼▼▼ 修正: URLの内容から環境を自動判定 ▼▼▼
        # Azurite (エミュレータ) の特徴: 
        # 1. ホストが 127.0.0.1 または localhost
        # 2. パスの先頭がアカウント名 (デフォルトは devstoreaccount1)
        is_emulator = (
            "127.0.0.1" in parsed.netloc or 
            "localhost" in parsed.netloc or 
            (len(parts) > 0 and parts[0] == "devstoreaccount1")
        )

        if is_emulator:
             # エミュレータの場合: /account_name/container_name/blob_name...
             if len(parts) >= 3:
                 container_name = parts[1]
                 blob_name = "/".join(parts[2:])
        else:
             # 本番(Azure)の場合: /container_name/blob_name...
             if len(parts) >= 2:
                 container_name = parts[0]
                 blob_name = "/".join(parts[1:])
        # ▲▲▲ 修正終了 ▲▲▲
        
        if not container_name or not blob_name:
            # パース失敗時はrequestsで直接取得を試みる
            import requests
            try:
                res = requests.get(blob_url, timeout=5)
                if res.status_code == 200:
                    return res.content
            except:
                pass
            return None

        # BlobClientを使って内部ネットワーク経由でダウンロード
        blob_service_client = get_blob_service_client()
        if not blob_service_client:
            return None
            
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        
        return blob_client.download_blob().readall()

    except Exception as e:
        logger.error(f"Failed to download bytes from blob url {blob_url}: {e}")
        return None
    
def upload_chat_attachment_to_blob(file_bytes: bytes, session_id: str, filename: str) -> str | None:
    """
    チャット添付ファイルを一時保存用コンテナにアップロードし、アクセス可能なURLを返す。
    """
    # ユニークなファイル名を生成 (session_id/uuid_filename)
    unique_id = uuid.uuid4().hex[:8]
    blob_name = f"{session_id}/{unique_id}_{filename}"
    
    # 修正後のストレージクラスの upload_bytes_to_blob を使用
    # container_name を明示的に指定する
    return upload_bytes_to_blob(
        data=file_bytes, 
        blob_name=blob_name, 
        container_name=config.AZURE_STORAGE_TMP_CONTAINER
    )

def delete_blobs_by_urls(urls: list[str]):
    """
    Blob URLのリストを受け取り、それらをすべて削除する。
    """
    if not urls:
        return

    client = get_blob_service_client()
    if not client:
        return

    for url in urls:
        try:
            # URLからコンテナ名とBlob名を抽出 (download_bytes_from_blob_url と同様のロジック)
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            path = unquote(parsed.path)
            parts = path.strip('/').split('/')
            
            # エミュレータと本番の判定
            is_emulator = ("127.0.0.1" in parsed.netloc or "localhost" in parsed.netloc or 
                           (len(parts) > 0 and parts[0] == "devstoreaccount1"))
            
            if is_emulator and len(parts) >= 3:
                container_name = parts[1]
                blob_name = "/".join(parts[2:])
            elif not is_emulator and len(parts) >= 2:
                container_name = parts[0]
                blob_name = "/".join(parts[1:])
            else:
                continue

            # 削除実行
            blob_client = client.get_blob_client(container=container_name, blob=blob_name)
            if blob_client.exists():
                blob_client.delete_blob()
                logger.info(f"Cleanup: Deleted blob {container_name}/{blob_name}")
        except Exception as e:
            logger.warning(f"Failed to delete blob during cleanup: {url}, error: {e}")
