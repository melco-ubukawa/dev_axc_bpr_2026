import logging
import json
import io
import os
import re
import tempfile
from google.genai import types
from google.genai.errors import ClientError
import config
from .file_processor import extract_file_content_to_part
from .gemini import get_gemini_client
# 【修正】GCSダウンロード用の関数を追加インポート
from .storage import download_bytes_from_blob_url, download_gcs_blob_to_bytes

# 動画の長さ取得用にOpenCVをインポート
try:
    import cv2
except ImportError:
    cv2 = None

logger = logging.getLogger(__name__)

def _get_video_duration_cv2(file_storage) -> float:
    """
    FileStorageオブジェクトから動画の長さ（秒）を取得するヘルパー関数
    """
    if cv2 is None:
        logger.warning("OpenCV (cv2) not installed. Cannot estimate video tokens accurately.")
        return 0.0

    try:
        # ポインタを先頭に
        file_storage.seek(0)
        
        # 一時ファイルに保存して読み込む
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_storage.filename)[1]) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name

        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            logger.warning(f"Failed to open video file for token estimation: {file_storage.filename}")
            os.remove(tmp_path)
            return 0.0

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = 0.0
        if fps > 0:
            duration = frame_count / fps
        
        cap.release()
        os.remove(tmp_path)
        
        # ポインタを戻す
        file_storage.seek(0)
        return duration

    except Exception as e:
        logger.error(f"Error getting video duration: {e}")
        return 0.0

def validate_token_limit(files, prompt_text: str, file_references_json: str = None, model_name: str = None):
    """
    アップロードされたファイル(直接) + ファイル参照(クラウド) + プロンプト(履歴含む)の
    合計トークン数をGemini APIで計測し、上限チェックを行う。
    動画ファイルはAPIに送信せず、ローカルで長さを計測してトークン数を概算加算する。
    """
    if not model_name:
        model_name = config.CHAT_MODEL_NAME

    parts = []
    processed_identifiers = set()
    
    # 動画トークンの概算用カウンタ
    estimated_video_tokens = 0
    # 動画トークン換算レート (Gemini 1.5 Pro/Flash: 映像263 + 音声32 ≈ 300 tokens/sec)
    TOKENS_PER_VIDEO_SECOND = 300

    # --- ヘルパー: テキスト/ソースコードかどうかの判定 ---
    def _is_source_code_or_text(mime_type, filename):
        if not mime_type: mime_type = ""
        if not filename: filename = ""
        
        if mime_type.startswith("text/") or mime_type in [
            "application/json", "application/xml", "application/javascript", 
            "application/x-python-code", "text/x-python", "application/x-sh"
        ]:
            return True
            
        text_extensions = {
            '.txt', '.md', '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', 
            '.json', '.xml', '.yaml', '.yml', '.sh', '.bat', '.csv', '.svg', '.sql'
        }
        ext = os.path.splitext(filename)[1].lower()
        return ext in text_extensions

    # --- ヘルパー: 動画ファイルかどうかの判定 ---
    def _is_video_file(filename):
        if not filename: return False
        video_extensions = {'.mp4', '.mov', '.avi', '.wmv', '.flv', '.mkv', '.webm', '.mpeg', '.mpg', '.m4v'}
        return os.path.splitext(filename)[1].lower() in video_extensions

    # --- 内部ヘルパー: ファイル参照情報からPartを作成 ---
    def _create_part_from_ref(ref_data):
        uri = ref_data.get('gcs_uri') or ref_data.get('url')
        mime_type = ref_data.get('mime_type')
        filename = ref_data.get('name', 'unknown_file')

        if uri and uri in processed_identifiers: return None
        if not uri and filename in processed_identifiers: return None
        if not uri: return None

        part = None
        
        # 動画ファイルの場合はURI参照であってもAPIカウント時にダウンロードが発生する可能性があるため、
        # GCS URIならAPIに任せ、それ以外は無視するか概算する（ここでは簡易的にAPIに任せる）
        # ※ GCS上の動画はAPIが直接アクセスできるため count_tokens も正確に行える
        
        if uri.startswith("gs://"):
            if _is_source_code_or_text(mime_type, filename):
                try:
                    file_bytes = download_gcs_blob_to_bytes(uri)
                    if file_bytes:
                        file_obj = io.BytesIO(file_bytes)
                        file_obj.filename = filename
                        part = extract_file_content_to_part(file_obj)
                except Exception:
                    part = types.Part.from_uri(file_uri=uri, mime_type=mime_type or "application/octet-stream")
            else:
                part = types.Part.from_uri(file_uri=uri, mime_type=mime_type or "application/octet-stream")

        elif os.path.exists(uri):
            try:
                with open(uri, 'rb') as f: file_bytes = f.read()
                file_obj = io.BytesIO(file_bytes); file_obj.filename = filename; file_obj.mimetype = mime_type
                part = extract_file_content_to_part(file_obj)
            except Exception: pass

        elif uri.startswith("http"):            
            is_text = _is_source_code_or_text(mime_type, filename)
            if is_text:
                try:
                    file_bytes = download_bytes_from_blob_url(uri)
                    if file_bytes:
                        file_obj = io.BytesIO(file_bytes); file_obj.filename = filename; file_obj.mimetype = mime_type
                        part = extract_file_content_to_part(file_obj)
                except Exception: pass
        
        if part:
            if uri: processed_identifiers.add(uri)
            if filename: processed_identifiers.add(filename)
            
        return part

    # 1. 新規: 直接アップロードされたファイルの処理
    if files:
        for file in files:
            if not file or not file.filename:
                continue
            
            # --- 【修正】動画ファイルの特別処理 ---
            if _is_video_file(file.filename):
                logger.info(f"TokenCheck: Video file detected: {file.filename}. Calculating estimated tokens locally.")
                duration = _get_video_duration_cv2(file)
                video_tokens = int(duration * TOKENS_PER_VIDEO_SECOND)
                estimated_video_tokens += video_tokens
                processed_identifiers.add(file.filename)
                logger.info(f"TokenCheck: Video '{file.filename}' duration: {duration:.2f}s, Estimated tokens: {video_tokens}")
                continue
            # ------------------------------------

            try:
                file.seek(0)
                part = extract_file_content_to_part(file)
                if part:
                    parts.append(part)
                    processed_identifiers.add(file.filename)
            except Exception as e:
                logger.warning(f"TokenCheck: Failed to extract content from {file.filename}: {e}")
            finally:
                file.seek(0)

    # 2. 新規: ファイル参照（GCS URI / Azure Blob URL）をPart化
    if file_references_json:
        try:
            references = json.loads(file_references_json)
            if isinstance(references, list):
                for ref in references:
                    # GCS上の動画はAPIが直接カウントできるため、ここではPartとして追加するだけで良い
                    part = _create_part_from_ref(ref)
                    if part:
                        parts.append(part)
        except json.JSONDecodeError:
            pass

    # 3. 履歴: プロンプトおよび会話履歴の処理
    if prompt_text:
        try:
            messages = json.loads(prompt_text)
            if isinstance(messages, list):
                for msg in messages:
                    content = msg.get('content')
                    if content:
                        text_content = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
                        parts.append(types.Part.from_text(text=text_content))
                    
                    attachments = msg.get('attachments', [])
                    for attachment in attachments:
                        part = _create_part_from_ref(attachment)
                        if part:
                            parts.append(part)
            else:
                parts.append(types.Part.from_text(text=prompt_text))
        except json.JSONDecodeError:
            parts.append(types.Part.from_text(text=prompt_text))

    # APIカウント対象が何もない場合でも、動画トークンがあればチェックする
    api_total_tokens = 0
    if parts:
        try:
            client = get_gemini_client()
            contents = [types.Content(role="user", parts=parts)]
            
            response = client.models.count_tokens(
                model=model_name,
                contents=contents
            )
            api_total_tokens = response.total_tokens
        except ClientError as e:
            error_msg = str(e)
            logger.error(f"Gemini API Error during token counting: {e}")
            # エラーハンドリング（省略）
            if "400" in error_msg or "INVALID_ARGUMENT" in error_msg:
                 raise ValueError("リクエストが無効、またはトークン上限を超えています。")
            else:
                # APIエラーでも動画トークンだけで判定できるなら続行、そうでなければエラー
                if estimated_video_tokens == 0:
                    raise ValueError(f"トークン数の計測に失敗しました。(API Error: {error_msg})")

    # 合計トークン数を計算
    total_tokens = api_total_tokens + estimated_video_tokens
    
    logger.info(f"Token Check Final Result: {total_tokens:,} tokens (API: {api_total_tokens:,} + Video Est: {estimated_video_tokens:,}) / Limit: {config.MAX_ALLOWED_TOKENS:,}")

    if total_tokens > config.MAX_ALLOWED_TOKENS:
        raise ValueError(
            f"合計トークン数（{total_tokens:,}）が上限（{config.MAX_ALLOWED_TOKENS:,}）を超えています。\n"
            "ファイルを減らすか、内容を分割してください。"
        )