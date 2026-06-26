import logging
import time
import json
import re
import os
from datetime import datetime
from gevent import queue
from gevent.event import Event
from flask import request

from google.cloud import speech
from google.genai import types

from .. import socketio
import config 

from .gemini import generate_with_gemini
from .cosmos_db import get_sessions_container
from .storage import _upload_text_to_gcs, get_main_gcs_client

logger = logging.getLogger(__name__)

# --- グローバル変数 ---
speech_queues = {}
stop_events = {}
meeting_contexts = {}
active_stt_workers = set()  # 実行中STTワーカーのSIDセット（多重起動防止）

# --- 翻訳機能: 対応言語定義 ---
TRANSLATION_LANG_LABELS: dict[str, str] = {
    "ja-JP":  "日本語",
    "en-US":  "English",
    "cmn-CN": "中文（简体）",
    "ko-KR":  "한국어",
    "fr-FR":  "Français",
    "de-DE":  "Deutsch",
    "es-ES":  "Español",
    "pt-BR":  "Português",
}
SUPPORTED_LANG_CODES: frozenset[str] = frozenset(TRANSLATION_LANG_LABELS.keys())

# --- ヘルパー関数 ---

def _validate_lang_code(sid: str, target_lang: str) -> bool:
    """target_lang が SUPPORTED_LANG_CODES に含まれるか検証する。不正値の場合は error_message を emit して False を返す。"""
    if target_lang not in SUPPORTED_LANG_CODES:
        logger.warning(f"[{sid}] Unsupported lang code requested: '{target_lang}'")
        socketio.emit('error_message',
                      {'data': f'未対応の言語コードです: {target_lang}'},
                      room=sid)
        return False
    return True


def _get_formatted_glossary_instruction(user_id):
    """ユーザーの用語集を取得してAI向けの指示テキストを生成する"""
    if not user_id:
        return ""
    try:
        container = get_sessions_container()
        if not container:
            return ""
        
        settings_id = f"settings_{user_id}"
        item = container.read_item(item=settings_id, partition_key=user_id)
        glossary = item.get('glossary', [])
        
        if not glossary:
            return ""

        instruction = "\n\n## 用語集定義 (最優先)\n以下のような用語が出現した場合、指定された「意味」や「コンテキスト」を最優先して解釈・使用してください。\n"
        for entry in glossary:
            term = entry.get('term', '')
            reading = entry.get('reading', '')
            meaning = entry.get('meaning', '')
            if term:
                line = f"- {term}"
                if reading: line += f" ({reading})"
                if meaning: line += f": {meaning}"
                instruction += line + "\n"
        return instruction
    except Exception as e:
        logger.warning(f"Failed to load glossary for user {user_id}: {e}")
        return ""

# --- GCS削除用ヘルパー ---
def _delete_gcs_folder(folder_prefix):
    """指定されたプレフィックス（フォルダ）以下の全オブジェクトを削除"""
    bucket_name = os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET_NAME")
    if not bucket_name: return
    
    try:
        storage_client = get_main_gcs_client()
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=folder_prefix))
        
        if blobs:
            bucket.delete_blobs(blobs)
            logger.info(f"Deleted GCS folder: {folder_prefix} ({len(blobs)} files)")
    except Exception as e:
        logger.error(f"Failed to delete GCS folder {folder_prefix}: {e}")

# --- 外部から呼び出されるセットアップ関数 ---
def setup_meeting_context(sid, interval, purpose, context_parts, user_info, duration=60):
    """
    routes.pyのAPIから呼び出され、会議設定を保存する。
    handle_start_recognition が先に設定した transcript_buffer 等を保護するため、
    完全置換ではなく .update() で更新する。
    """
    update_data = {
        "active": True,
        "interval": interval,
        "context_parts": context_parts, # GCS URIのリスト
        "last_analysis_time": time.time(),
        "user_info": user_info,
        "purpose": purpose,
        "duration": duration,
        "inactive_since": None,         # クリーンアップ用タイマー
    }
    if sid not in meeting_contexts:
        # 初回: 全フィールドを設定
        update_data["transcript_buffer"] = []
        update_data["last_analysis_index"] = 0
        update_data["gcs_transcript_uri"] = None
        meeting_contexts[sid] = update_data
    else:
        # 既存: handle_start_recognition が設定済みの transcript_buffer 等を保護
        meeting_contexts[sid].update(update_data)
    logger.info(f"[{sid}] Meeting context setup complete. Interval: {interval}min, Duration: {duration}min")

# --- AI分析ロジック（共通化） ---
def _generate_meeting_insight(sid):
    """
    指定されたセッションIDに対してAI分析を実行し、結果をemitする。
    定期実行と手動実行の両方から呼ばれる。
    """
    context = meeting_contexts.get(sid)
    if not context: return

    gcs_bucket_name = os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET_NAME")
    
    # 全会話ログを取得
    full_buffer = context.get("transcript_buffer", [])
    last_index = context.get("last_analysis_index", 0)
    
    # 新しい発言があるか確認（手動実行の場合は、前回分析時より増えていなくても実行したい場合があるかもしれないが、
    # 基本的には差分がないと分析の意味が薄い。ただし「今すぐ」の要望に応えるため、
    # 少なくともバッファが存在すれば実行するようにする）
    if not full_buffer:
        logger.info(f"[{sid}] No transcript to analyze.")
        return

    # 全文結合（GCS保存用）
    full_text = "\n".join(full_buffer)
    
    # GCSにテキストファイルとして保存（上書き更新）
    if gcs_bucket_name:
        transcript_blob_name = f"meeting_data/{sid}/transcript.txt"
        gcs_uri = _upload_text_to_gcs(gcs_bucket_name, transcript_blob_name, full_text)
        
        if gcs_uri:
            context["gcs_transcript_uri"] = gcs_uri
            logger.info(f"[{sid}] Transcript synced to GCS: {gcs_uri}")

    # インデックスと時刻を更新
    context["last_analysis_index"] = len(full_buffer)
    context["last_analysis_time"] = time.time()

    try:
        user_info = context.get("user_info", {})
        glossary_instruction = _get_formatted_glossary_instruction(user_info.get("userId"))
        
        system_instruction = """あなたは優秀な会議ファシリテーター兼書記です。
提供された「会議の目的」「資料」「これまでの経緯」「直近の会話」を総合的に分析し、会議がゴールに向かうよう支援してください。
特に「直近の会話」の内容に基づいて、最新の状況を反映した要約とアクションアイテムを抽出してください。
以下のJSONフォーマットのみを出力してください。Markdown記法や説明文は不要です。

{
  "summary": "直近の議論の要約（文脈を踏まえて3〜5行）",
  "action_items": [
    {"task": "タスク内容", "owner": "担当者（未定なら'未定'）", "deadline": "期限（未定なら'未定'）"}
  ],
  "missing_info": ["期限や担当者が決まっていないタスクへの指摘", "議論が脱線している場合の指摘など。特になければ空配列"],
  "facilitation": "次の議論への誘導や、会議の目的に対する進捗状況のアドバイス"
}"""
        # プロンプトの構成
        parts = context.get("context_parts", [])[:] # コピーを作成
        
        # GCS上の議事録ファイルをコンテキストに追加
        if context.get("gcs_transcript_uri"):
            parts.append(types.Part.from_uri(
                file_uri=context["gcs_transcript_uri"], 
                mime_type="text/plain"
            ))
        else:
            # GCS失敗時はメモリ上のテキストを直接渡す（フォールバック）
            parts.append(types.Part.from_text(text=f"【会議ログ】\n{full_text}"))

        # 手動実行か定期実行かに関わらず、現時点までの状況を分析させる
        prompt_text = "これまでの議論を踏まえて、現在の会議状況を分析してください。"
        parts.append(types.Part.from_text(text=prompt_text))

        contents = [types.Content(role="user", parts=parts)]

        logger.info(f"[{sid}] Generating AI insight...")
        
        response_json_str, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            contents=contents,
            system_instruction=system_instruction + glossary_instruction,
            max_output_tokens=65535,
            temperature=0.3,
            user_info=user_info,
            feature_category="AI会議コパイロット",
            generation_config_override={"response_mime_type": "application/json"}
        )
        
        if response_json_str:
            try:
                insight_data = json.loads(response_json_str)
                insight_data["timestamp"] = datetime.now().strftime('%H:%M')
                socketio.emit('ai-insight', insight_data, room=sid)
                logger.info(f"[{sid}] AI Insight emitted.")
            except json.JSONDecodeError:
                logger.error(f"[{sid}] Failed to parse AI Insight JSON.")
    except Exception as e:
        logger.error(f"[{sid}] Error in Meeting Copilot: {e}", exc_info=True)


# --- バックグラウンドタスク ---

def run_meeting_copilot(sid):
    """
    会議コパイロットの定期実行タスク。
    """
    logger.info(f"[{sid}] Meeting Copilot task started.")
    socketio.sleep(2)
    
    while True:
        context = meeting_contexts.get(sid)
        if not context or not context.get("active", False):
            if context:
                context["inactive_since"] = time.time()
            logger.info(f"[{sid}] Meeting Copilot task stopped.")
            break

        interval_minutes = context.get("interval", 20)
        interval_sec = interval_minutes * 60
        
        last_analysis = context.get("last_analysis_time", 0)
        now = time.time()
        elapsed = now - last_analysis

        if elapsed >= interval_sec:
            # 定期実行タイミングで分析を実行
            _generate_meeting_insight(sid)
            
        socketio.sleep(1)

def speech_to_text_streaming(client_sid, language_code):
    """
    Google Speech-to-Text APIでストリーミング認識を行うワーカー。
    """
    local_stt_client = None
    # 【Fix5】ワーカー起動をマーク
    active_stt_workers.add(client_sid)

    try:
        logger.info(f"[{client_sid}] Initializing speech recognition worker...")

        try:
            # ここでインスタンス化
            local_stt_client = speech.SpeechClient()
        except Exception as e:
            logger.error(f"[{client_sid}] Failed to initialize Speech-to-Text client: {e}", exc_info=True)
            socketio.emit('error_message', {'data': '音声認識サービスの初期化に失敗しました。'}, room=client_sid)
            return

        config_stt = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=language_code,
            enable_automatic_punctuation=True,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config_stt,
            interim_results=True,
        )

        audio_q = speech_queues.get(client_sid)
        stop_evt = stop_events.get(client_sid)

        if not audio_q or not stop_evt:
            logger.error(f"[{client_sid}] Queue or Event not initialized.")
            return

        def request_generator():
            while not stop_evt.is_set():
                try:
                    chunk = audio_q.get(timeout=1.0)
                    if chunk is None:
                        return
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)
                except queue.Empty:
                    continue

        logger.info(f"[{client_sid}] Starting speech recognition loop (Infinite Streaming).")
        
        start_time = time.time()
        general_error_count = 0
        MAX_GENERAL_RETRIES = 3

        while not stop_evt.is_set():
            # --- 時間制限チェック (ループ内で毎回最新の設定を取得) ---
            context = meeting_contexts.get(client_sid)
            max_duration_sec = 60 * 60 * 2 # デフォルト2時間

            if context and "duration" in context:
                try:
                    max_duration_sec = int(context["duration"]) * 60
                except (ValueError, TypeError):
                    pass

            if time.time() - start_time > max_duration_sec:
                logger.info(f"[{client_sid}] Max duration exceeded ({max_duration_sec}s). Stopping server-side recognition.")
                socketio.emit('status_update', {'data': '設定された制限時間を超過したため、サーバー側で認識を停止しました。'}, room=client_sid)
                socketio.emit('force_stop', {}, room=client_sid)
                break

            try:
                responses = local_stt_client.streaming_recognize(
                    config=streaming_config,
                    requests=request_generator()
                )

                for response in responses:
                    if not response.results: continue
                    result = response.results[0]
                    if not result.alternatives: continue

                    transcript = result.alternatives[0].transcript
                    is_final = result.is_final

                    socketio.emit('recognition_result', {'transcript': transcript, 'is_final': is_final}, room=client_sid)

                    if is_final:
                        context = meeting_contexts.get(client_sid)
                        if context and context.get("active"):
                            timestamp = datetime.now().strftime('%H:%M:%S')
                            context["transcript_buffer"].append(f"[{timestamp}] {transcript}")
                            logger.debug(f"[{client_sid}] Buffered: {transcript}")
                    # 【Fix3】geventのイベントループに制御を返し、WebSocketハンドシェイク等をブロックしない
                    socketio.sleep(0)

                # ストリームが正常終了した場合、stop_eventがセットされるまで再起動
                logger.info(f"[{client_sid}] STT stream ended normally. Restarting...")
                general_error_count = 0  # 正常終了時はエラーカウントをリセット
                continue

            except Exception as e:
                error_str = str(e)
                # 400エラーやストリーム制限エラー時の再接続ロジック
                if "400" in error_str and "Exceeded maximum allowed stream duration" in error_str:
                    logger.info(f"[{client_sid}] Google STT stream limit reached. Restarting stream...")
                    general_error_count = 0
                    continue
                else:
                    general_error_count += 1
                    if not stop_evt.is_set():
                        logger.error(f"[{client_sid}] Error during speech streaming (attempt {general_error_count}/{MAX_GENERAL_RETRIES}): {e}", exc_info=True)
                        if general_error_count >= MAX_GENERAL_RETRIES:
                            socketio.emit('error_message', {'data': f'音声認識中にエラーが発生し、リトライ上限に達しました: {e}'}, room=client_sid)
                            break
                        else:
                            # バックオフ付きリトライ
                            backoff_sec = general_error_count * 2
                            logger.info(f"[{client_sid}] Retrying STT stream in {backoff_sec}s...")
                            socketio.sleep(backoff_sec)
                            continue
                    break

        logger.info(f"[{client_sid}] Speech recognition loop ended.")
        # STTワーカー終了をクライアントに通知
        socketio.emit('recognition_ended', {}, room=client_sid)

    except Exception as fatal_e:
        logger.critical(f"[{client_sid}] Fatal error in speech_to_text_streaming: {fatal_e}", exc_info=True)
        socketio.emit('error_message', {'data': 'サーバー内部エラーにより音声認識が停止しました。'}, room=client_sid)
    
    finally:
        # 【Fix5】ワーカー終了をマーク
        active_stt_workers.discard(client_sid)
        if local_stt_client:
            try:
                close_method = getattr(local_stt_client, 'close', None)
                if callable(close_method):
                    close_method()
                    logger.debug(f"[{client_sid}] Speech client closed successfully.")
                else:
                    local_stt_client.transport.close()
                    logger.debug(f"[{client_sid}] Speech client transport closed.")
            except Exception as close_error:
                logger.warning(f"[{client_sid}] Error closing speech client: {close_error}")
            
            local_stt_client = None

# --- クリーンアップタスク ---
CONTEXT_TIMEOUT_SECONDS = 600 # 10分間再接続がなければデータ削除

def cleanup_stale_contexts():
    """
    定期的に実行され、終了した会議のメモリとGCSデータを削除する
    """
    while True:
        socketio.sleep(60) # 1分ごとにチェック
        current_time = time.time()
        sids_to_remove = []

        for sid, context in list(meeting_contexts.items()):
            # アクティブでなく、かつタイムアウト時間を過ぎている場合
            if not context.get("active", False):
                inactive_since = context.get("inactive_since")
                if inactive_since and (current_time - inactive_since > CONTEXT_TIMEOUT_SECONDS):
                    sids_to_remove.append(sid)
        
        for sid in sids_to_remove:
            logger.info(f"[{sid}] Cleaning up stale meeting data...")
            
            # 1. GCSデータの削除
            _delete_gcs_folder(f"meeting_data/{sid}/")
            
            # 2. メモリからの削除
            meeting_contexts.pop(sid, None)
            
            # 3. 関連リソースのクリーンアップ
            if sid in speech_queues: speech_queues.pop(sid, None)
            if sid in stop_events: stop_events.pop(sid, None)
            
            logger.info(f"[{sid}] Cleanup complete.")

# --- SocketIOイベントハンドラ ---

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    logger.info(f"Client disconnected: {sid}")
    
    if sid in stop_events: stop_events[sid].set()
    if sid in speech_queues: speech_queues[sid].put(None)
    
    if sid in meeting_contexts:
        meeting_contexts[sid]["active"] = False
        # クリーンアップ用タイマーセット
        meeting_contexts[sid]["inactive_since"] = time.time()
        
        speech_queues.pop(sid, None)
        stop_events.pop(sid, None)

@socketio.on('start_recognition')
def handle_start_recognition(data):
    sid = request.sid
    language_code = data.get('language', 'ja-JP')
    carry_over = data.get('carry_over_transcript', [])
    if not isinstance(carry_over, list):
        carry_over = []
    logger.info(f"[{sid}] Received start_recognition request. Language: {language_code}, carry_over_lines: {len(carry_over)}")

    # 【Fix5】旧STTワーカーが残存している場合は終了を待機してから新規起動（多重起動防止）
    if sid in active_stt_workers:
        logger.info(f"[{sid}] Waiting for previous STT worker to terminate...")
        for _ in range(20):  # 100ms × 20 = 最大2秒待機
            if sid not in active_stt_workers:
                break
            socketio.sleep(0.1)
        if sid in active_stt_workers:
            logger.warning(f"[{sid}] Previous STT worker did not terminate within 2s. Proceeding anyway.")

    speech_queues[sid] = queue.Queue()
    stop_events[sid] = Event()
    stop_events[sid].clear()

    if sid not in meeting_contexts:
        meeting_contexts[sid] = {
            "active": True, "interval": 20, "context_parts": [],
            # 前回録音分のテキストをキャリーオーバーとして引き継ぐ
            "transcript_buffer": list(carry_over),
            "last_analysis_time": time.time(), "user_info": {}, "purpose": "",
            "inactive_since": None, "gcs_transcript_uri": None
        }
    else:
        meeting_contexts[sid]["active"] = True
        meeting_contexts[sid]["inactive_since"] = None # 再接続時はタイマークリア

    socketio.start_background_task(target=speech_to_text_streaming, client_sid=sid, language_code=language_code)
    socketio.start_background_task(target=run_meeting_copilot, sid=sid)

    socketio.emit('status_update', {'data': '音声認識とAI分析を開始しました。'}, room=sid)

@socketio.on('audio_stream')
def handle_audio_stream(audio_data):
    sid = request.sid
    if sid in speech_queues and not stop_events.get(sid, Event()).is_set():
        speech_queues[sid].put(audio_data)

@socketio.on('stop_recognition')
def handle_stop_recognition():
    sid = request.sid
    logger.info(f"[{sid}] Received stop_recognition request.")
    
    if sid in stop_events: stop_events[sid].set()
    if sid in speech_queues: speech_queues[sid].put(None)
    if sid in meeting_contexts: 
        meeting_contexts[sid]["active"] = False
        meeting_contexts[sid]["inactive_since"] = time.time()
        
    socketio.emit('status_update', {'data': '音声認識を停止しました。'}, room=sid)

# --- 新規追加: 手動AI分析リクエストハンドラ ---
@socketio.on('request_ai_insight')
def handle_request_ai_insight():
    sid = request.sid
    logger.info(f"[{sid}] Manual AI insight requested.")
    # バックグラウンドタスクとして実行し、イベントループをブロックしない
    socketio.start_background_task(target=_generate_meeting_insight, sid=sid)

# --- 会議評価ロジック ---
def _generate_meeting_evaluation(sid):
    """
    会議の文字起こし内容を評価スコアリングシステムに基づいて評価する。
    plan.pngに記載された評価基準を使用する。
    """
    context = meeting_contexts.get(sid)
    if not context:
        return

    full_buffer = context.get("transcript_buffer", [])
    if not full_buffer:
        logger.info(f"[{sid}] No transcript to evaluate.")
        socketio.emit('meeting-evaluation', {
            "error": "評価対象の会話ログがありません。録音中に会議評価を実行してください。"
        }, room=sid)
        return

    full_text = "\n".join(full_buffer)
    purpose = context.get("purpose", "")

    try:
        user_info = context.get("user_info", {})
        glossary_instruction = _get_formatted_glossary_instruction(user_info.get("userId"))

        system_instruction = """あなたは会議品質の評価エキスパートです。
提供された「会議の目的」と「会議の文字起こし」を分析し、以下の評価基準に従って会議を評価してください。

## 評価スコアリングシステム

### 基本設定
- 初期スコア: 50点

### 加点要素 (+1点/回)

#### 1. 感謝・敬意 (respect_words)
以下のようなフレーズが含まれる発言:
- ありがとうございます / 助かります / 感謝します
- 共有・説明ありがとうございます
- おっしゃる通りです / 確かにそうですね / 良い視点です
- 参考になります / 理解しました / 承知しました

#### 2. 相槌・同意 (listening_words)
以下のようなフレーズが含まれる発言:
- はい / そうですね / なるほど / たしかに
- 了解です / いいですね / わかります

#### 3. 丁寧な反論・補足 (polite_disagreement)
以下のようなフレーズが含まれる発言:
- 一点だけ補足させてください
- 少し違う観点からですが / 別の切り口ですが
- 懸念があります / 確認したい点があります
- 質問があります / 前提を確認させてください
- 代替案を検討したいです

### 減点要素 (重い減点)

#### 1. 否定的・攻撃的 (-50点/回)
- 意味がわからない / それは違うでしょ / 全然だめ
- あり得ない / 話にならない / レベルが低い
- あなたのせい / 責任を果たしていない / どうしてできないの

#### 2. 皮肉・嫌味 (-30点/回)
- どうせ無理ですよね / やっと理解したんですか
- まあ好きにすれば / はいはい分かりましたよ

### 特殊ルール (状況判定)

#### 1. 建設的な反論 (+10点)
条件: 反論あり AND 対案あり

#### 2. 否定のみの反論 (-10点)
条件: 反論あり AND 対案なし

#### 3. 建設的対案の提示 (+10点)
条件: 「もし〜なら」「別案として」等の条件付き賛成や提案

#### 4. なあなあ会議 (-20点)
条件: 「懸念」「リスク」「反対」「確認」に類する発言がゼロ

#### 5. ツルの一声 (-20点)
条件: 特定話者の発言シェアが60%超 AND 他者が短答（「はい」「承知」「了解」のみ、または3単語以内の肯定的返答）のみ

### 判定ロジック (AIの識別基準)
- 「反論」の判定: キーワード: 「しかし」「一方で」「異論」「反対」等を含む発言
- 「対案」の判定: キーワード: 「代替案」「別案」「〜してみては」「〜するのはどうか」等
- 「短答」の判定: 内容: 「はい」「承知」「了解」のみ、または3単語以内の肯定的な返答

### 評価換算 (★)
- ★5: 80点以上
- ★4: 65〜79点
- ★3: 50〜64点
- ★2: 35〜49点
- ★1: 34点以下

## 出力フォーマット
以下のJSONフォーマットのみを出力してください。Markdown記法や説明文は不要です。

{
  "summary": "会議全体の要約（3〜5行。会議の目的に対する達成度も含める）",
  "total_score": 数値（最終スコア）,
  "star_rating": 数値（1〜5の★評価）,
  "plus_details": {
    "respect_words": {"count": 数値, "examples": ["発言例1", "発言例2"]},
    "listening_words": {"count": 数値, "examples": ["発言例1"]},
    "polite_disagreement": {"count": 数値, "examples": ["発言例1"]}
  },
  "minus_details": {
    "aggressive": {"count": 数値, "penalty_per": -50, "examples": ["発言例1"]},
    "sarcasm": {"count": 数値, "penalty_per": -30, "examples": ["発言例1"]}
  },
  "special_rules": [
    {"rule": "ルール名", "applied": true/false, "score": 加減点値, "reason": "判定理由"}
  ],
  "score_breakdown": "50(初期) + X(加点) - Y(減点) + Z(特殊) = 最終スコア の形式",
  "improvement_suggestions": ["改善提案1", "改善提案2", "改善提案3"]
}"""

        parts = []

        if purpose:
            parts.append(types.Part.from_text(text=f"【会議の目的】\n{purpose}"))

        # GCS上の議事録ファイルがあればそれを参照、なければメモリ上のテキスト
        if context.get("gcs_transcript_uri"):
            parts.append(types.Part.from_uri(
                file_uri=context["gcs_transcript_uri"],
                mime_type="text/plain"
            ))
        else:
            parts.append(types.Part.from_text(text=f"【会議の文字起こし】\n{full_text}"))

        parts.append(types.Part.from_text(
            text="上記の会議の文字起こしを評価基準に基づいて評価してください。"
        ))

        contents = [types.Content(role="user", parts=parts)]

        logger.info(f"[{sid}] Generating meeting evaluation...")

        response_json_str, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            contents=contents,
            system_instruction=system_instruction + glossary_instruction,
            max_output_tokens=65535,
            temperature=0.3,
            user_info=user_info,
            feature_category="会議評価",
            generation_config_override={"response_mime_type": "application/json"}
        )

        if response_json_str:
            try:
                evaluation_data = json.loads(response_json_str)
                evaluation_data["timestamp"] = datetime.now().strftime('%H:%M')
                evaluation_data["purpose"] = purpose
                socketio.emit('meeting-evaluation', evaluation_data, room=sid)
                logger.info(f"[{sid}] Meeting evaluation emitted.")
            except json.JSONDecodeError:
                logger.error(f"[{sid}] Failed to parse meeting evaluation JSON.")
                socketio.emit('meeting-evaluation', {
                    "error": "評価結果の解析に失敗しました。"
                }, room=sid)
    except Exception as e:
        logger.error(f"[{sid}] Error in meeting evaluation: {e}", exc_info=True)
        socketio.emit('meeting-evaluation', {
            "error": f"評価処理中にエラーが発生しました: {str(e)}"
        }, room=sid)


@socketio.on('request_meeting_evaluation')
def handle_request_meeting_evaluation():
    sid = request.sid
    logger.info(f"[{sid}] Meeting evaluation requested.")
    socketio.start_background_task(target=_generate_meeting_evaluation, sid=sid)


# --- 翻訳機能 ---

def _generate_insight_translation(sid: str, insight_data: dict, target_lang: str):
    """
    クライアントから受け取った AI インサイト JSON を target_lang に翻訳し、
    translation_result イベントで返す。
    """
    lang_label = TRANSLATION_LANG_LABELS[target_lang]
    logger.info(f"[{sid}] Generating insight translation to {lang_label}...")

    system_instruction = (
        f"あなたは高精度の翻訳エンジンです。\n"
        f"入力されたJSONの各テキストフィールドを {lang_label} に翻訳してください。\n"
        f"JSONの構造・キー名・数値・null・真偽値は一切変更せず、テキスト値のみを翻訳してください。\n"
        f"翻訳後の同じ構造のJSONのみを出力してください。Markdown記法や説明文は不要です。"
    )

    # タイムスタンプは翻訳対象外のため除外して送信
    timestamp = insight_data.get("timestamp")
    translatable = {k: v for k, v in insight_data.items() if k != "timestamp"}

    try:
        prompt_text = json.dumps(translatable, ensure_ascii=False)
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt_text)])]

        response_json_str, _ = generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME,
            contents=contents,
            system_instruction=system_instruction,
            max_output_tokens=65535,
            temperature=0.1,
            user_info={},
            feature_category="会議翻訳(AI分析)",
            generation_config_override={"response_mime_type": "application/json"},
        )

        if not response_json_str:
            raise ValueError("Gemini returned empty response.")

        translated = json.loads(response_json_str)
        translated["target_lang"] = target_lang
        translated["lang_label"] = lang_label
        if timestamp:
            translated["timestamp"] = timestamp

        socketio.emit('translation_result', translated, room=sid)
        logger.info(f"[{sid}] Insight translation emitted (lang={lang_label}).")

    except json.JSONDecodeError as e:
        logger.error(f"[{sid}] Failed to parse insight translation JSON: {e}")
        socketio.emit('error_message',
                      {'data': 'AI分析結果の翻訳中にエラーが発生しました（JSONパース失敗）。'},
                      room=sid)
    except Exception as e:
        logger.error(f"[{sid}] Error in insight translation: {e}", exc_info=True)
        socketio.emit('error_message',
                      {'data': f'AI分析結果の翻訳中にエラーが発生しました: {e}'},
                      room=sid)


def _generate_transcript_translation(sid: str, target_lang: str):
    """
    サーバー側の transcript_buffer を target_lang に翻訳し、
    transcript_translation_result イベントで返す。
    """
    context = meeting_contexts.get(sid)
    lang_label = TRANSLATION_LANG_LABELS[target_lang]
    logger.info(f"[{sid}] Generating transcript translation to {lang_label}...")

    full_text = "\n".join(context.get("transcript_buffer", []))

    system_instruction = (
        f"あなたは高精度の翻訳エンジンです。\n"
        f"入力された会話ログ（タイムスタンプ付きの複数行テキスト）を {lang_label} に翻訳してください。\n\n"
        f"ルール:\n"
        f"- \"[HH:MM:SS]\" 形式のタイムスタンプは翻訳せずそのまま保持してください。\n"
        f"- 各行を独立して翻訳し、行の順序を変えないでください。\n"
        f'- 翻訳後のテキストを JSON 配列 {{"lines": ["翻訳行1", "翻訳行2", ...]}} として出力してください。\n'
        f"- Markdown記法や説明文は不要です。"
    )

    try:
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=full_text)])]

        response_json_str, _ = generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME,
            contents=contents,
            system_instruction=system_instruction,
            max_output_tokens=65535,
            temperature=0.1,
            user_info=context.get("user_info", {}),
            feature_category="会議翻訳(会話ログ)",
            generation_config_override={"response_mime_type": "application/json"},
        )

        if not response_json_str:
            raise ValueError("Gemini returned empty response.")

        result = json.loads(response_json_str)
        result["target_lang"] = target_lang
        result["lang_label"] = lang_label

        socketio.emit('transcript_translation_result', result, room=sid)
        logger.info(f"[{sid}] Transcript translation emitted (lang={lang_label}, lines={len(result.get('lines', []))}).")

    except json.JSONDecodeError as e:
        logger.error(f"[{sid}] Failed to parse transcript translation JSON: {e}")
        socketio.emit('error_message',
                      {'data': '会話ログの翻訳中にエラーが発生しました（JSONパース失敗）。'},
                      room=sid)
    except Exception as e:
        logger.error(f"[{sid}] Error in transcript translation: {e}", exc_info=True)
        socketio.emit('error_message',
                      {'data': f'会話ログの翻訳中にエラーが発生しました: {e}'},
                      room=sid)


@socketio.on('request_translation')
def handle_request_translation(data):
    """AI分析結果の翻訳リクエストを受け取り、バックグラウンドで翻訳を実行する。"""
    sid = request.sid
    target_lang = data.get('target_lang', '')
    insight_data = data.get('insight_data')

    if not _validate_lang_code(sid, target_lang):
        return

    if not insight_data or not isinstance(insight_data, dict):
        logger.warning(f"[{sid}] request_translation: insight_data is missing or invalid.")
        socketio.emit('error_message',
                      {'data': '翻訳対象のデータがありません。先にAI分析を実行してください。'},
                      room=sid)
        return

    logger.info(f"[{sid}] Insight translation requested. Target: {target_lang}")
    socketio.start_background_task(
        target=_generate_insight_translation,
        sid=sid,
        insight_data=insight_data,
        target_lang=target_lang,
    )


@socketio.on('request_transcript_translation')
def handle_request_transcript_translation(data):
    """会話ログ全文の翻訳リクエストを受け取り、バックグラウンドで翻訳を実行する。"""
    sid = request.sid
    target_lang = data.get('target_lang', '')

    if not _validate_lang_code(sid, target_lang):
        return

    context = meeting_contexts.get(sid)
    if not context or not context.get('transcript_buffer'):
        logger.warning(f"[{sid}] request_transcript_translation: transcript_buffer is empty or context not found.")
        socketio.emit('error_message',
                      {'data': '翻訳対象の会話ログがありません。録音中に実行してください。'},
                      room=sid)
        return

    logger.info(f"[{sid}] Transcript translation requested. Target: {target_lang}")
    socketio.start_background_task(
        target=_generate_transcript_translation,
        sid=sid,
        target_lang=target_lang,
    )