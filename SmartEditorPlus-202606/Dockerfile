# Dockerfile (修正版)

# --- ビルドステージ ---
# C拡張を持つライブラリをビルドするために、必要なツールを含むイメージを使用
FROM python:3.12-bookworm AS builder

# aptのキャッシュをクリーンに保つ設定 (BuildKit不要な記述に修正)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmagic-dev \
    && rm -rf /var/lib/apt/lists/*

# 仮想環境を作成
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 依存関係ファイルを先にコピー
COPY requirements.txt .
# pipのキャッシュマウントを削除し、BuildKitに依存しないように修正
RUN pip install --no-cache-dir -r requirements.txt


# --- 最終ステージ ---
# アプリケーション実行用の軽量なイメージを使用
FROM python:3.12-bookworm

ENV MPLCONFIGDIR /tmp/matplotlib

# セキュリティ向上のため、非rootユーザーを作成・使用
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin -c "Docker user" app
WORKDIR /app

# 実行に必要なシステムライブラリをインストール (BuildKit不要な記述に修正)
# イメージサイズを削減し、依存関係を最適化
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基本ライブラリ
    libmagic1 \
    libgomp1 \
    # ← ★ lightgbm, xgboost等の実行に必須
    wget \
    tar \
    ca-certificates \
    curl \
    # 日本語フォント関連
    fonts-noto-cjk \
    fontconfig \
    # scientific computingライブラリの依存
    libgfortran5 \
    libopenblas0 \
    # PlantUML実行環境
    default-jre-headless \
    graphviz \
    ffmpeg \
    libgl1-mesa-glx \
    # Marp CLIの依存関係 (chromiumを削除し、最小限のライブラリに)
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdbus-1-3 \
    libexpat1 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 \
    libpango-1.0-0 libpangocairo-1.0-0 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxss1 libxtst6 \
    # fc-cacheコマンドの実行
    && fc-cache -fv \
    # 不要になったパッケージキャッシュをクリーンアップ
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Marp CLIのインストール
ARG MARP_CLI_VERSION=4.1.2
RUN wget "https://github.com/marp-team/marp-cli/releases/download/v${MARP_CLI_VERSION}/marp-cli-v${MARP_CLI_VERSION}-linux.tar.gz" -O marp-cli.tar.gz && \
  tar -xzf marp-cli.tar.gz -C /usr/local/bin marp && \
  chmod +x /usr/local/bin/marp && \
  rm marp-cli.tar.gz

# PlantUMLのインストール
ARG PLANTUML_VERSION=1.2024.8
RUN wget -O /app/plantuml.jar \
  "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar" && \
  chmod 644 /app/plantuml.jar

# ビルド時のバージョンを実行時の環境変数として設定
ENV PLANTUML_VERSION=${PLANTUML_VERSION}

# ビルドステージからインストール済みのPythonパッケージをコピー
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder $VIRTUAL_ENV $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV FFMPEG_PATH /usr/bin/ffmpeg

# アプリケーションコードをコピーする前に、appユーザーとしてmatplotlibのフォントキャッシュをビルドする
# これにより、実行時に書き込みが発生しなくなる
RUN chown -R app:app /app
USER app
RUN python -c "import matplotlib.font_manager"
USER root

# アプリケーションコードをコピー
# pip installの後に行うことで、コード変更時のビルドキャッシュを有効活用
COPY --chown=app:app . .

# ユーザーをappに切り替え
USER app

# Cloud Run / Azure App Serviceがリッスンするポート
EXPOSE 8080

# コンテナ起動コマンド（PORT環境変数をAzure App Serviceが提供するものに合わせる）
CMD exec gunicorn --bind "0.0.0.0:8080" --workers 1 --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --timeout 0 "run:app"
