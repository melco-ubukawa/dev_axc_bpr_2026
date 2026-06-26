"""アプリ全体のログ設定を行うモジュール。

Flask-3-Log-Request-ID の RequestIDLogFilter を用いて request_id をログに埋め込みつつ、
すべてのロガーが同じフォーマットで出力されるように初期化する。

どのモジュールでも基本的には以下のようにして利用する想定:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("message")

ログ設定そのものは、アプリ起動時に init_logging() を 1 回だけ呼び出す。
"""

from flask_log_request_id import RequestIDLogFilter

import logging
import os
import threading


_task_local = threading.local()


def set_request_id_for_current_thread(request_id: str | None) -> None:
    """バックグラウンドタスク用に、このスレッドに紐づく request_id を設定する。"""

    _task_local.request_id = request_id


def get_request_id_for_current_thread() -> str | None:
    """現在のスレッドに紐づいている request_id を取得する。なければ None を返す。"""

    return getattr(_task_local, "request_id", None)


class SafeRequestIDLogFilter(RequestIDLogFilter):
    """request_id が存在しない場合でも必ずフィールドを埋めるフィルタ。

    - HTTP リクエスト中: Flask-3-Log-Request-ID が設定した request_id をそのまま利用
    - それ以外: record.request_id が存在しなければ "-" をセット

    これにより、%(request_id)s を含むフォーマッタを常に安全に利用できる。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            # 親クラス側で、リクエストコンテキストがあれば request_id を付与
            super().filter(record)
        except Exception:
            # ライブラリの実装変更などで例外が出てもログ自体は流す
            pass

        # HTTP リクエストコンテキスト外（例: ThreadPoolExecutor のワーカーなど）の場合は、
        # スレッドローカルに設定された request_id を優先的に利用する
        thread_request_id = get_request_id_for_current_thread()
        if (not hasattr(record, "request_id") or record.request_id is None) and thread_request_id:
            record.request_id = thread_request_id

        # それでもまだ request_id が無い場合は "null" をセット
        if not hasattr(record, "request_id") or record.request_id is None:
            record.request_id = "null"

        return True


def init_logging() -> None:
    """アプリ全体のログ設定を初期化する。

    - ルートロガーにハンドラを 1 本だけ設定
    - 指定されたフォーマットで request_id を含めて出力
    - LOG_LEVEL 環境変数でログレベルを制御 (デフォルト INFO)
    - Azure SDK など一部ライブラリのログレベルも調整
    """

    # LOG_LEVEL 環境変数からログレベルを取得 (例: DEBUG, INFO, WARNING, ERROR, CRITICAL)
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 既存ハンドラがある場合は一度クリアして、重複出力を防ぐ
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s - request_id=%(request_id)s - %(name)s - %(funcName)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # request_id がない場合も "-" を埋める安全なフィルタを付与
    handler.addFilter(SafeRequestIDLogFilter())

    root_logger.addHandler(handler)

    # Azure SDK などのログレベル調整
    azure_logger = logging.getLogger("azure.core.pipeline.policies")
    azure_logger.setLevel(logging.WARNING)

    # 必要に応じて Werkzeug などのログレベルもここで調整可能
    # werkzeug_logger = logging.getLogger("werkzeug")
    # werkzeug_logger.setLevel(logging.INFO)

    # ここで返り値は特に持たず、副作用として設定を完了する
    return None
