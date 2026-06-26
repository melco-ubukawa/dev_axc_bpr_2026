# smarteditorplus/__init__.py

import os
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
from whitenoise import WhiteNoise
from flask_log_request_id import RequestID

# グローバルスコープで拡張機能のインスタンスを作成
# これらは後でファクトリ関数内でアプリケーションに紐付けられます
socketio = SocketIO(async_mode='gevent')

from .utils import cosmos_db, storage, file_processor, gemini, auth, customLogger

def create_app():
    """
    アプリケーションファクトリ関数。
    Flaskアプリケーションのインスタンスを生成し、設定、拡張機能、Blueprintを登録します。
    """
    app = Flask(__name__, 
                template_folder='../templates',  # パッケージ外のtemplatesフォルダを指定
                static_folder='../static')     # パッケージ外のstaticフォルダを指定

    # --- 設定の読み込み ---
    # config.pyから設定を読み込む
    # この書き方により、app.config経由で設定値にアクセスできるようになります。
    app.config.from_pyfile('../config.py')

    # --- ログ設定 ---
    # Flask-3-Log-Request-ID による request_id 発行と、アプリ全体のログフォーマット初期化
    # RequestID は Flask のリクエストコンテキストに request_id を紐付ける
    RequestID(app)
    # customLogger でルートロガーのフォーマット・ハンドラ・ログレベルなどを統一
    customLogger.init_logging()

    # --- Flask拡張機能の初期化 ---
    CORS(app)
    socketio.init_app(app)

    # --- ミドルウェアの設定 ---
    # ProxyFixとWhiteNoiseの設定
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )
    # WhiteNoiseは静的ファイルを効率的に配信するために使用します
    # static_prefixは'/static'がデフォルトなので通常は指定不要です
    app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/')
    
    # --- Blueprintの登録 ---
    # このセクションは、後のステップで各機能のBlueprintを作成した際に追加していきます。
    # 例:
    # from .blueprints import chat
    # app.register_blueprint(chat.bp)

    # --- アプリケーションコンテキスト内での処理 ---
    with app.app_context():
        # ここに、アプリケーションの初期化時に一度だけ実行したい処理を記述できます。
        # (例: データベーステーブルの作成など)
        pass

    # --- ルート（エンドポイント）の登録 ---
    with app.app_context():
        from . import routes
        from .utils import meeting_handler 
        from m365_search import m365_search_bp
        app.register_blueprint(m365_search_bp)

    # テンプレート全体で参照できるデプロイ情報を注入
    @app.context_processor
    def inject_deploy_info():
        # 優先順: 環境変数 -> app.config -> デフォルト文字列
        app_title = os.environ.get('APP_TITLE') or app.config.get('APP_TITLE') or 'スマートエディタ Plus'
        # show_dev_message を判定する。
        env_val = os.environ.get('IS_DEVELOPMENT')
        if env_val is not None:
            show_dev = str(env_val).lower() in ('1', 'true', 'yes', 'on')
        else:
            show_dev = bool(app.config.get('IS_DEVELOPMENT', False))

        # dev_message は常に定義してテンプレートに渡す（環境変数 -> app.config -> デフォルト）
        dev_message = os.environ.get('DEV_MESSAGE') or app.config.get('DEV_MESSAGE') or '開発メンバー以外の開発環境の利用は禁止です。'

        return dict(deploy_title=app_title, deploy_h1=app_title, show_dev_message=show_dev, dev_message=dev_message)
    return app
