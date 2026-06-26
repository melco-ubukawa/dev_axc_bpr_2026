# run.py
import os

# ★★★ 根本対策: デバッグモードではgeventを無効化 ★★★
# Flaskがデバッグモードで起動するかどうかを先に判定する
IS_DEBUG_MODE = os.environ.get('FLASK_DEBUG') == '1'

if not IS_DEBUG_MODE:
    from gevent import monkey
    import grpc.experimental.gevent
    # 他のライブラリがインポートされる前に、必ず最初にパッチを適用する
    monkey.patch_all()
    os.environ['GRPC_PYTHON_ENABLE_FORK_SUPPORT'] = '0'
    grpc.experimental.gevent.init_gevent()

from smarteditorplus import create_app, socketio
from smarteditorplus.data_analyzer_routes import data_analyzer_bp
from smarteditorplus.design_routes import design_bp
from smarteditorplus.sharepoint_tool_routes import sharepoint_tool_bp

# アプリケーションファクトリを呼び出してappインスタンスを生成
app = create_app()

app.register_blueprint(data_analyzer_bp, url_prefix='/analyzer')
app.register_blueprint(design_bp)
app.register_blueprint(sharepoint_tool_bp)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    # debug=True は create_app 内の FLASK_DEBUG で制御されるので、
    # ここでは allow_unsafe_werkzeug のみ指定
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)