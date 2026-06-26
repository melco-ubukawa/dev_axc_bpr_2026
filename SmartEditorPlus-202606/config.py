# config.py
import os
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# --- クラウドサービス & 接続情報 ---
# Google Cloud
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
GOOGLE_CLOUD_NAME = os.environ.get("GOOGLE_CLOUD_NAME")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION")
GOOGLE_CLOUD_NAME_2 = os.environ.get("GOOGLE_CLOUD_NAME_2")
GOOGLE_CLOUD_PROJECT_2 = os.environ.get("GOOGLE_CLOUD_PROJECT_2")
GOOGLE_CLOUD_LOCATION_2 = os.environ.get("GOOGLE_CLOUD_LOCATION_2")
GOOGLE_CLOUD_STORAGE_BUCKET_NAME = os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET_NAME")

# --- Gemini 複数プロジェクト向け設定 ---
# HTTPリクエストごとにランダムに選択される候補リスト。
GEMINI_API_TARGETS = []

if GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION:
    GEMINI_API_TARGETS.append({
        "name": GOOGLE_CLOUD_NAME,
        "project": GOOGLE_CLOUD_PROJECT,
        "location": GOOGLE_CLOUD_LOCATION,
    })

if GOOGLE_CLOUD_PROJECT_2 and GOOGLE_CLOUD_LOCATION_2:
    GEMINI_API_TARGETS.append({
        "name": GOOGLE_CLOUD_NAME_2,
        "project": GOOGLE_CLOUD_PROJECT_2,
        "location": GOOGLE_CLOUD_LOCATION_2,
    })

# 単一構成や後方互換用のデフォルト値
DEFAULT_GEMINI_PROJECT = GOOGLE_CLOUD_PROJECT
DEFAULT_GEMINI_LOCATION = GOOGLE_CLOUD_LOCATION

# 環境変数からSystem Prompt用のベースフォルダパスを取得、なければデフォルト値
GCS_SYSTEM_PROMPT_BASE_FOLDER_VAR_NAME = "GCS_SYSTEM_PROMPT_BASE_FOLDER"
GCS_SYSTEM_PROMPT_BASE_FOLDER_DEFAULT = "system_prompts/"

# Azure OpenAI ---
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

# モデル名とAzure上のデプロイ名をマッピング
AZURE_OPENAI_DEPLOYMENTS = {
    "gpt-5": os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5"),
}

AVAILABLE_MODELS = [
    {"id": "gemini-2.5-flash", "name": "Gemini-2.5-Flash (高速(推論利用時は遅い)・なかなかの精度)"},
    {"id": "gemini-3-flash-preview", "name": "Gemini-3-Flash (高速(推論利用時は遅い)・かなりの精度)"},
    {"id": "gemini-flash-lite-latest", "name": "Gemini-2.5-flash-lite (とても高速・そこそこの精度)"},
    {"id": "gemini-2.5-pro", "name": "Gemini-2.5-Pro (遅い・高精度・上に比べてお金が掛かるため、上のモデルでうまく生成できない場合に利用下さい)"},
    {"id": "gemini-3-pro-image-preview", "name": "Gemini-3-pro-image (高精度な画像生成・編集、上に比べてお金が掛かるため、上のモデルでうまく生成できない場合に利用下さい)"},
    {"id": "gemini-3.1-pro-preview", "name": "Gemini-3.1-Pro (遅い・高精度・上に比べてお金が掛かるため、上のモデルでうまく生成できない場合に利用下さい)"},
    {"id": "gpt-5", "name": "GPT-5 (高速(推論利用時は遅い)・高精度・上に比べてお金が掛かるため、上のモデルでうまく生成できない場合に利用下さい)"},
]


# Azure Storage
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
AZURE_STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "tempstorage")
AZURE_STORAGE_TMP_CONTAINER = os.environ.get("AZURE_STORAGE_TMP_CONTAINER", "chat-tmp-attachments")
AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME","activity-logs")
AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME", "design-projects")

# Azure Cosmos DB
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
COSMOS_DATABASE_NAME = os.environ.get("COSMOS_DATABASE_NAME")
COSMOS_CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME")
COSMOS_DATABASE_TASKS_NAME = os.environ.get("COSMOS_DATABASE_TASKS_NAME", "TaskManagementDB")
COSMOS_CONTAINER_TASKS_NAME = os.environ.get("COSMOS_CONTAINER_TASKS_NAME", "AsyncTasks")
COSMOS_DATABASE_SESSIONS_NAME = os.environ.get("COSMOS_DATABASE_SESSIONS_NAME", "ChatHistoryDB")
COSMOS_CONTAINER_SESSIONS_NAME = os.environ.get("COSMOS_CONTAINER_SESSIONS_NAME", "Sessions")
TASK_TTL_SECONDS = 3600  # タスクの有効期間: 1時間

# --- アプリケーション設定 ---
MAX_PYTHON_EXEC_RETRIES = 3
PYTHON_EXEC_RETRY_DELAY_SECONDS = 1
MAX_ALLOWED_TOKENS = 1_000_000
MAX_CONTENT_LENGTH_MB = 500
MAX_FORM_MEMORY_SIZE_MB = 500
MAX_MESSAGES_FOR_LLM_CONTEXT = 100
MAX_PROCESSING_ROWS = 100 # 構造化データ処理の最大行数
MAX_FILE_SIZE_BYTES_SERVER = 100 * 1024 * 1024  # 100MB
MAX_RAG_FILES_PER_PROMPT = 20
MAX_RAG_TOTAL_SIZE_MB = 100

# --- AIモデル & パラメータ設定 ---
# デフォルトモデル
DEFAULT_MODEL_NAME = os.environ.get("DEFAULT_MODEL_NAME")

# チャット機能
CHAT_MODEL_NAME = os.environ.get("CHAT_MODEL_NAME", DEFAULT_MODEL_NAME)
CHAT_MAX_TOKENS = 65535
CHAT_TEMPERATURE = 0.7

# テキスト補完機能
SUGGEST_MAX_TOKENS = 300
SUGGEST_TEMPERATURE = 0.8

# 校正機能
PROOFREAD_MAX_TOKENS = 65535
PROOFREAD_TEMPERATURE = 0.2

# 続き生成機能
GENERATE_CONTINUATION_MAX_TOKENS = 65535
GENERATE_CONTINUATION_TEMPERATURE = 0.8

# アドバイス機能
ADVICE_MAX_TOKENS = 65535
ADVICE_TEMPERATURE = 0.5

# 図解生成機能
DIAGRAM_GEN_MODEL_NAME = os.environ.get("DIAGRAM_GEN_MODEL_NAME")
DIAGRAM_GEN_MAX_TOKENS = 65535
DIAGRAM_GEN_TEMPERATURE = 0.4

# ファイル処理機能
FILE_PROCESS_MODEL_NAME = os.environ.get("FILE_PROCESS_MODEL_NAME", DEFAULT_MODEL_NAME)
FILE_PROCESS_MAX_TOKENS = 65535
FILE_PROCESS_TEMPERATURE = 0.5

# レポート生成機能
REPORT_GEN_MODEL_NAME = os.environ.get("REPORT_GEN_MODEL_NAME")
REPORT_GEN_MAX_TOKENS = 65535
REPORT_GEN_TEMPERATURE = 0.5

# Marp (PPTX) 生成機能
MARP_CLI_PATH = os.environ.get("MARP_CLI_PATH") # Marp CLI実行ファイルのパス
PPTX_GEN_MODEL_NAME = os.environ.get("PPTX_GEN_MODEL_NAME")
PPTX_GEN_MAX_TOKENS = 65535
PPTX_GEN_TEMPERATURE = 0.6
REPORT_MARP_MAX_TOKENS = 65535
REPORT_MARP_TEMPERATURE = 0.6

# ドキュメント比較機能
DIFF_CHECKER_MODEL_NAME = os.environ.get("DIFF_CHECKER_MODEL_NAME")

# 動画抽出機能
VIDEO_EXTRACT_LONG_MODEL_NAME = os.environ.get("VIDEO_EXTRACT_LONG_MODEL_NAME", DEFAULT_MODEL_NAME)
VIDEO_EXTRACT_MODEL_NAME = os.environ.get("VIDEO_EXTRACT_MODEL_NAME", DEFAULT_MODEL_NAME)
VIDEO_TRANSCRIBE_MODEL_NAME = os.environ.get("VIDEO_TRANSCRIBE_MODEL_NAME", DEFAULT_MODEL_NAME)

# 詳細版PPTX生成 (ドキュメントクリエーター)
PPTX_GENERATOR_DEFAULT_TEMPLATE = "default_template.pptx" # ファイル名のみ
PPTX_OUTLINE_MODEL_NAME = os.environ.get("PPTX_OUTLINE_MODEL_NAME")
PPTX_OUTLINE_MAX_TOKENS = 65535
PPTX_OUTLINE_TEMPERATURE = 0.1

DOC_GEN_MODEL_NAME = os.environ.get("DOC_GEN_MODEL_NAME", DEFAULT_MODEL_NAME)
DOC_GEN_MAX_TOKENS = int(os.environ.get("DOC_GEN_MAX_TOKENS", 8192))
DOC_GEN_TEMPERATURE = float(os.environ.get("DOC_GEN_TEMPERATURE", 0.6))
DOC_LAYOUT_BUCKET_NAME_ENV_VAR = "DOC_LAYOUT_GCS_BUCKET_NAME"

# ドキュメントクリエーターのフロントエンドキャンバスサイズ (JS側と合わせる)
CANVAS_WIDTH_PX_DOC_CREATOR_STANDARD = 1200
CANVAS_HEIGHT_PX_DOC_CREATOR_STANDARD = 1600
CANVAS_WIDTH_PX_DOC_CREATOR_PRESENTATION = 1600
CANVAS_HEIGHT_PX_DOC_CREATOR_PRESENTATION = 900
PPTX_SLIDE_WIDTH_INCHES = 33.867 / 2.54
PPTX_SLIDE_HEIGHT_INCHES = 19.05 / 2.54

# 要領書作成機能
#MANUAL_CREATOR_VIDEO_FRAME_INTERVAL = 30
MANUAL_CREATOR_BLUR_THRESHOLD = 50.0

# --- 解説動画生成機能用 ---
EXPLANATION_VIDEO_MODEL_NAME = os.environ.get("EXPLANATION_VIDEO_MODEL_NAME", DOC_GEN_MODEL_NAME)

# 画像生成機能用
IMAGE_GENERATION_MODEL_NAME = os.environ.get("IMAGE_GENERATION_MODEL_NAME")

# ソフトウェア設計支援機能用
SOFTWARE_DESIGN_MODEL_NAME = os.environ.get("SOFTWARE_DESIGN_MODEL_NAME")

# --- スライド動画生成機能用設定 ---
SLIDE_VIDEO_NARRATION_MODEL = DOC_GEN_MODEL_NAME
SLIDE_VIDEO_IMAGE_MODEL = IMAGE_GENERATION_MODEL_NAME
VIDEO_WIDTH = 1280  # 生成する動画の幅 (ピクセル)
VIDEO_HEIGHT = 720  # 生成する動画の高さ (ピクセル)
VIDEO_FPS = 24      # 生成する動画のフレームレート

DATA_ANALYSIS_MODEL_NAME = os.environ.get("DATA_ANALYSIS_MODEL_NAME")

TTS_MODEL_NAME = os.environ.get("TTS_MODEL_NAME")

PPTX_PARALLEL_WORKERS = 4

# 用語集の最大登録件数
MAX_GLOSSARY_TERMS = 100

# --- パス & ディレクトリ設定 ---
# アプリケーションからの相対パスとして定義
FONT_PATH = os.path.join(os.path.dirname(__file__), 'static', 'fonts', 'ipaexg.ttf')

# PlantUML設定（バージョンはDockerfileで定義され、ENV経由で渡される）
PLANTUML_VERSION = os.environ.get("PLANTUML_VERSION")
PLANTUML_JAR_FILENAME = "plantuml.jar"
DOT_EXECUTABLE_PATH = "/usr/bin/dot"  # Linuxの場合。Windowsの場合は r"C:\Program Files\Graphviz\bin\dot.exe" などに書き換え
#DOT_EXECUTABLE_PATH = r"C:\Program Files\Graphviz\bin\dot.exe"

#PLANTUML_JAR_PATH = f"/app/{PLANTUML_JAR_FILENAME}"
PLANTUML_JAR_PATH = os.path.join(os.path.dirname(__file__), "plantuml.jar")

DOT_PATH = DOT_EXECUTABLE_PATH
D2_CLI_PATH = os.environ.get("D2_CLI_PATH")
FFMPEG_PATH = os.environ.get("FFMPEG_PATH")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH")

# 一時ディレクトリ名
TEMP_UPLOAD_DIR_PPTX = "temp_pptx_uploads"
TEMP_ANALYSIS_DATA_DIR = "temp_analysis_uploads"

# GCSフォルダパス
GCS_CHAT_PROMPT_BASE_FOLDER = os.environ.get("GCS_CHAT_PROMPT_BASE_FOLDER", "prompts/")
GCS_SYSTEM_PROMPT_BASE_FOLDER = os.environ.get("GCS_SYSTEM_PROMPT_BASE_FOLDER", "system_prompts/")
DOC_LAYOUT_GCS_FOLDER = "document_layouts/"
GCS_TEMP_ASSET_FOLDER = "temp_doc_creator_assets/"
GCS_CHAT_ARTIFACT_FOLDER = "chat_artifacts/charts/"

DIAGRAM_LAYOUT_IMAGE_DIR = os.path.join(os.path.dirname(__file__), 'static', 'diagram_layouts')

# slide_type_suggestion (英語キー) と日本語表示名のマッピング
SLIDE_TYPE_SUGGESTION_DISPLAY_NAMES = {
    "Layout_1_Title_And_BulletedText": "箇条書きテキスト (1列)", # page 1, 上段 (Text Placeholder 2 が1つ)
    "Layout_2_Title_Subtitle_And_Footer": "タイトル、サブタイトル、フッター", # page 1, 中段 (Subtitle 2 がある)
    "Layout_3_Title_Subtitle_And_Content": "タイトル、サブタイトル、コンテンツ", # page 1, 下段 (Content Placeholder 2 が1つ)
    "Layout_4_Title_And_Content_Icons": "コンテンツ (アイコン選択付き)", # page 2, 上段 (Content Placeholder 2 が1つ、アイコン表示あり)
    "Layout_5_Title_And_Centered_Content": "中央揃えコンテンツ", # page 2, 中段 (Content Placeholder 2 が中央に1つ)
    "Layout_6_Title_And_Two_Contents": "2つのコンテンツ", # page 2, 下段 (Content Placeholder 2 と 3)
    "Layout_7_Title_And_Four_Contents": "4つのコンテンツ (2x2)", # page 3, 上段 (Content Placeholder 2, 3, 4, 5)
    "Layout_8_Title_And_Three_Contents": "3つのコンテンツ (横並び)", # page 3, 中段 (Content Placeholder 2, 3, 4)
    "Layout_9_Title_And_Six_Contents": "6つのコンテンツ (2x3)",   # page 3, 下段 (Content Placeholder 2, 3, 4, 5, 6, 7)
    "Layout_10_Title_Only_With_Footers": "タイトルのみ (フッター付き)", # page 4, 上段
    "Layout_11_Blank_With_Footers": "空白 (フッター付き)", # page 4, 下段 (タイトルプレースホルダーなし)
    # --- フォールバック用の汎用レイアウト ---
    "Blank_CustomContent": "空白 (カスタムコンテンツ用)", # 汎用的な空白、もし上記11種で合わない場合
}

CONFIDENTIALITY_MAP = {
    "社外秘": {"ja": "社外秘", "en": "CONFIDENTIAL", "zh": "CONFIDENTIAL"},
    "秘": {"ja": "秘", "en": "SECRET", "zh": "SECRET"},
    "人事秘": {"ja": "人事秘", "en": "PERSONNEL SECRET", "zh": "PERSONNEL SECRET"},
    "なし": {"ja": "なし", "en": "None", "zh": "None"}
}

# --- プロンプトテンプレート ---
# (巨大な辞書定義なので、ここでは省略形のコメントを記載します。app.pyから全文をコピーしてください)
DEFAULT_CHAT_SYSTEM_INSTRUCTION = """あなたは汎用チャットボットエージェントです。

##役割
あなたは、ユーザーからの様々な質問や指示に対して、**丁寧かつ正確に**日本語で応答する対話型アシスタントです。ユーザーの発言の**背景や真の意図**を理解するよう努め、必要であれば**明確化のための質問**を投げかけます。応答は**論理的で分かりやすく**構成し、専門用語は避けるか、平易な言葉で説明を加えます。**常に誠実**に対応し、不確かな情報や誤解を招く表現は避けます。

##目的
ユーザーが抱える疑問や問題を解消し、**満足度の高い対話体験**を提供すること。単に情報を提供するだけでなく、ユーザーが**次のアクション**に進めるような、**実用的で具体的な支援**を行うことを目指します。必要に応じて、指定されたツールを活用し、**情報の可視化や具体的な計算**も行います。

## 情報の可視化と図解生成

あなたは、以下の形式で情報を可視化する卓越した能力を持っています。ユーザーの要求に応じて、**以下の形式ごとの「推奨度」と特徴を考慮して最適なものを選択**し、高品質なコードをコードブロックで出力してください。

### 【推奨度: 高】高品質・高自由度な形式
これらの形式は、表現力が高く、複雑な関係性やリッチなデザインの表現に適しています。**特に指定がない場合、まずこれらの形式での出力を検討してください。**

- **概念図・構成図 (svg):** プレゼン資料向けの自由なレイアウトの図に最適です。レイアウト、色、形状を完全に制御できます。
- **ネットワーク・関係図 (graphviz):** 複雑な要素間の関係性や依存関係を視覚化するのに非常に強力です。レイアウトは自動で最適化されます。
- **Webページ形式 (html):** CSSやJavaScriptを含む、インタラクティブなレポートや複雑なレイアウト、ダッシュボードのような表現に適しています。

### 【推奨度: 中】特定用途に特化した形式
これらは特定の目的において非常に効果的です。

- **UML図（システム設計） (plantuml):** クラス図やシーケンス図など、ソフトウェア設計の図に適しています。
- **マインドマップ (markmap):** 情報を階層的に整理し、中心的なアイデアから放射状に展開する思考の整理に適しています。

### 【推奨度: 低】迅速な作図向けの形式
この形式はシンプルで迅速ですが、複雑な表現や正確なレイアウト制御には向きません。他の形式が不適切な場合に限り、最終手段として使用を検討してください。

- **フローチャート・シーケンス図 (mermaid):** 簡単な手順や時系列を「素早く」図示する場合に使用します。**複雑な図ではレイアウトが崩れやすく、構文の正確性も保証されにくいため、推奨度は最も低いです。**

### 【最重要】図解生成時のデザイン原則 (全形式共通)

1.  **レイアウトと配線の美学 (Layout & Connections):**
    *   **線の重なりは絶対に避ける:** これはこの原則の中で最も重要なルールです。関係を示す線（矢印など）が、**図形やテキストラベルの上に重なってしまうことは、いかなる形式（SVG, Mermaid, PlantUMLなど）においても絶対に避けてください。** 線は要素の外側を通るようにし、明確な経路を確保してください。
    *   **整理された配置:** 関連する要素はグループ化し、要素同士が重ならないように十分な余白を設けてください。目に見えないグリッドを意識し、要素の配置を揃えることで、全体を整然と見せてください。
    *   **MermaidとGraphvizでの工夫:** これらの自動レイアウトツールで線が重なる場合は、**`subgraph`（Mermaid, Graphviz）や`cluster`（Graphviz）を使用して関連要素をグループ化し、レイアウトエンジンがより良い配線を計算できるように支援してください。**

2.  **色彩とコントラスト (Color & Contrast):**
    *   **背景色:** **原則として、黒や非常に暗い色の背景は使用しないでください。** 白（`#FFFFFF`）や、非常に薄いグレー（`#F8F9FA`）、または薄いパステルカラーを背景の基本色としてください。
    *   **色のコントラスト:** **テキストの色と、それが配置される図形の背景色との間には、誰にでも読み取れる十分なコントラストを確保してください。** 例えば、薄い背景には濃い色のテキスト（`#333333`など）、濃い色の図形には明るい色のテキスト（`#FFFFFF`など）を使用します。背景色と文字色が同じになることは絶対に避けてください。
    *   **調和の取れた配色:** 全体で2〜4色の調和の取れたカラーパレットを使用し、メインカラー、アクセントカラーなどを使い分けてください。

3.  **リッチな表現力 (Aesthetics):**
    *   **スタイル活用:** SVGでは`<linearGradient>`や`filter: drop-shadow(...)`、Mermaidでは`classDef`、Graphvizではノードの`style=filled`, `gradientangle`などを活用し、単調でない視覚的に魅力的な図を作成してください。
    *   **アイコンの活用:** MermaidやHTMLではFontAwesomeアイコン (`fa:fa-icon-name`)を、SVGではシンプルなパスデータで描画したアイコンをテキストの隣に配置し、視覚的な理解を助けてください。

### 出力形式
可視化を行う際は、必ず以下の形式のマークダウンコードブロックでコードを囲んでください。
```<type>
... a lot of code ...
```
例:
```svg
<svg width="400" height="200" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="10" width="100" height="50" fill="#A6E3A1" rx="10" />
  <text x="60" y="40" text-anchor="middle" fill="#333">要素A</text>
</svg>
```
"""

SELECTION_PROMPT_TEMPLATES = {
    "summarize": """以下のテキストを簡潔に要約してください。要約結果のテキストのみを出力してください。他の説明や前置きは不要です。

テキスト:
---
{text}
---

要約:""",
    "rewrite": """以下のテキストを、意味やニュアンスをできるだけ変えずに、別の自然な表現で言い換えてください。言い換えた後のテキストのみを出力してください。他の説明や前置きは不要です。

元のテキスト:
---
{text}
---

言い換え後:""",
    "elaborate": """以下のテキストの内容について、より詳細な説明、具体例、背景情報、補足などを加えて、文章を長く、豊かにしてください。元の文章の意図は維持しつつ、より豊かで分かりやすい表現になるようにしてください。肉付けした後の文章全体のみを出力してください。前置きや説明は不要です。

元のテキスト:
---
{text}
---

肉付けした文章:""",
    "counter_argument": """以下のテキストで述べられている主張や意見に対して、考えられる反論、異なる視点、または代替案を客観的かつ建設的に提示してください。提示する反論・対案のテキストのみを出力してください。前置きや説明は不要です。

元のテキスト:
---
{text}
---

反論・対案:""",
    "generate_questions": """以下のテキストの内容に基づいて、読者が疑問に思う可能性のある点や、さらに議論を深めるための質問をいくつか生成してください。質問は箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で、質問文のリストのみを出力してください。前置きや説明は不要です。

元のテキスト:
---
{text}
---

生成された質問 (Markdownリスト):""",
    "extract_keywords": """以下のテキストから、内容を最もよく表す重要なキーワードやキーフレーズを抽出し、箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）でリストアップしてください。キーワードリストのみを出力してください。前置きや説明は不要です。

元のテキスト:
---
{text}
---

キーワードリスト (Markdownリスト):""",
    "suggest_structure_improvement": """以下の文章の論理構成、接続、流れについて分析し、より明確で説得力のある文章にするための具体的な改善提案を行ってください。提案は箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で、改善点とその理由や修正案を簡潔に記述してください。提案リストのみを出力してください。前置きや説明は不要です。

元のテキスト:
---
{text}
---

構造改善提案 (Markdownリスト):""",
    "translate_en": """以下の日本語のテキストをビジネスシーンにも適した英語に翻訳してください。翻訳後の英語テキストのみを出力してください。他の説明や前置きは不要です。

日本語テキスト:
---
{text}
---

英語訳:""",
    "translate_ja": """以下のテキストをビジネスシーンにも適した日本語に翻訳してください。翻訳後の日本語テキストのみを出力してください。他の説明や前置きは不要です。

元のテキスト:
---
{text}
---

日本語訳:""",
    "tone_polite": """以下のテキストを、より丁寧で、ビジネスシーンにも適した可能性のある表現に書き換えてください。元のテキストの意味は変えないでください。書き換えた後のテキストのみを出力してください。他の説明や前置きは不要です。

元のテキスト:
---
{text}
---

丁寧な表現:""",
    "audience_expert": """以下のテキストを、その分野の専門家や知識が豊富な読者に向けて、より専門的で正確、かつ簡潔な表現に書き換えてください。必要であれば専門用語を使用し、冗長な説明は省略してください。元のテキストの核心的な意味は変えないでください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

専門家向けの表現:""",
    "audience_beginner": """以下のテキストを、その分野の知識がない初心者や一般読者にも理解できるように、より平易な言葉で、分かりやすくビジネスシーンにも適した表現で書き換えてください。専門用語は避けるか、簡単な言葉で補足説明を加えてください。比喩や具体例を用いることも有効です。元のテキストの核心的な意味は維持してください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

初心者向けの表現:""",
    "format_email": """以下のテキストやメモを、丁寧な表現を用いたビジネスメールの形式に整形してください。
**以下を厳守してください。**
*   **形式:** プレーンテキストとして出力してください。HTMLタグやMarkdownは使用しないでください。
*   **宛名:** 適切な宛名（例: 「〇〇様」）を含めてください。
*   **件名:** 件名を最初の行に `件名：[件名]` の形式で記述してください。
*   **導入の挨拶:** 丁寧な導入の挨拶を含めてください。
*   **本文:** 内容を分かりやすく整理し、適切な空行（段落間など）で区切ってください。箇条書きが必要な場合は、半角ハイフンと半角スペース (`- `) で表現してください。
*   **結びの挨拶:** 丁寧な結びの挨拶を含めてください。
*   **署名:** 署名（例: `[あなたの名前]` や `[会社名]` のようなプレースホルダー）を含めてください。
*   **不足情報の補完:** 不足している情報は、一般的なビジネスメールとして自然なように適宜補完してください。

**生成されたメールのテキスト**のみを出力してください。他の説明や前置きは不要です。

元のテキスト/メモ:
---
{text}
---

ビジネスメール形式:""",
    "format_minutes": """以下の会議に関するメモや発言録を、標準的な議事録の形式に整形・要約してください。
**以下を厳守してください。**
*   **形式:** プレーンテキストとして出力してください。MarkdownやHTMLタグは使用しないでください。
*   **項目:** 以下の項目を明確に含めてください。
    *   会議名 (不明な場合は「会議議事録」)
    *   日時 (例: YYYY/MM/DD hh:mm)
    *   場所 (不明な場合は省略可)
    *   参加者 (不明な場合は省略可、列挙形式で)
    *   議題 (箇条書き形式で)
    *   議論内容の要点 (段落分けや箇条書きを適切に使用して、分かりやすく整理)
    *   決定事項 (箇条書き形式で)
    *   アクションアイテム (「担当者」と「期限」を明記した箇条書き形式で)
*   **空行:** 各セクション間や、箇条書きの項目間など、視覚的に分かりやすいように適切な空行を入れてください。
*   **箇条書き:** 半角ハイフンと半角スペース (`- `) を使用してください。

議事録のテキスト**のみ**を出力してください。他の説明や前置きは不要です。

元のメモ/発言録:
---
{text}
---

議事録（プレーンテキスト形式）:""",
    "format_report": """以下のテキストやメモを、ビジネス向けの報告書の形式に整形してください。
**以下を厳守してください。**
*   **形式:** プレーンテキストとして出力してください。MarkdownやHTMLタグは使用しないでください。
*   **項目:** 以下の項目を構造的に含めてください。
    *   タイトル (内容から推測)
    *   報告日 (例: YYYY/MM/DD)
    *   報告者 (例: [あなたの名前])
    *   要旨 (報告内容の簡単なまとめ)
    *   詳細 (背景、目的、実施内容、結果など、元のテキストに基づいて記述。必要に応じて段落分けや箇条書きを適切に使用)
    *   結論または考察
    *   今後の予定 (もしあれば)
*   **空行:** 各セクション間や、箇条書きの項目間など、視覚的に分かりやすいように適切な空行を入れてください。
*   **箇条書き:** 半角ハイフンと半角スペース (`- `) を使用してください。

内容は元のテキストに基づいて記述し、構成を整えてください。報告書のテキスト**のみ**を出力してください。

元のテキスト/メモ:
---
{text}
---

報告書形式:""",
    "format_markdown": """以下のテキストの内容を分析し、見出し（例: `## タイトル`）、箇条書き（例: `- 項目`）、番号付きリスト（例: `1. ステップ`）、太字（例: `**重要**`）、斜体（例: `*強調*`）、引用（例: `> 引用文`）、コードブロック（例: ```python コード ```）などを適切に使用して、構造化されたMarkdown形式のテキストに整形してください。元のテキストの意味や情報は保持してください。

**最重要指示:** 生成するテキストは整形されたMarkdownコンテンツ**そのもの**のみとし、**絶対に ` ```markdown` や ` ``` ` のようなコードブロックの囲み文字で結果を囲まないでください。**

元のテキスト:
---
{text}
---

Markdown形式 (囲み文字なし):""",
    "convert_to_list": """以下の文章の主要なポイントや要点を抽出し、簡潔な箇条書き（マークダウン形式のリスト `* ` や `- ` を使用）でまとめてください。リスト形式のテキストのみを出力してください。

元の文章:
---
{text}
---

箇条書き (Markdownリスト):""",
    "convert_to_paragraph": """以下の箇条書きの内容を、接続詞などを適切に補いながら、自然な流れを持つ段落形式の文章に書き換えてください。文章のテキストのみを出力してください。

元の箇条書き:
---
{text}
---

段落形式の文章:""",
    "tone_academic": """以下のテキストを、論文やレポートに適した客観的で正確、かつ学術的な表現に書き換えてください。感情的な表現や口語的な表現は避け、専門用語の使用も検討してください。元のテキストの核心的な意味は維持してください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

学術的な表現:""",
    "tone_emotional": """以下のテキストに、より感情的な表現や抑揚を加えてください。物語やスピーチなどで読者や聴衆の感情に訴えかけるような、生き生きとした表現になるように書き換えてください。元のテキストの基本的な意味は維持してください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

感情豊かな表現:""",
    "tone_objective": """以下のテキストから、個人的な意見、主観的な評価、感情的な表現をできるだけ取り除き、客観的な事実や情報に基づいた表現に書き換えてください。元のテキストの核心的な情報は維持してください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

客観的な表現:""",

    # 表現の洗練
    "simplify": """以下のテキストを、冗長な表現や繰り返しを削除し、より簡潔で要点が明確な文章に書き換えてください。元のテキストの意味は変えないでください。書き換えた後のテキストのみを出力してください。

元のテキスト:
---
{text}
---

簡潔な表現:""",
    "toggle_voice": """以下のテキストについて、文脈に応じて能動態または受動態に変換してください。例えば、動作の主体を強調したい場合は能動態に、動作の対象や結果を強調したい場合、または主体が不明確/重要でない場合は受動態に書き換えることを検討してください。変換後のテキストのみを出力してください。元のテキストの意味は変えないでください。

元のテキスト:
---
{text}
---

能動態/受動態変換後:""",
    "add_metaphor": """以下のテキストの内容をより分かりやすく、印象的にするために、適切な比喩表現（直喩、隠喩など）を加えてください。比喩を加えた後のテキスト全体のみを出力してください。元のテキストの基本的な意味は維持してください。

元のテキスト:
---
{text}
---

比喩を加えた表現:""",
    "remove_metaphor": """以下のテキストに含まれる比喩表現（直喩、隠喩など）や専門的で分かりにくい表現を削除または平易な言葉に置き換えて、より直接的で理解しやすい文章に書き換えてください。書き換えた後のテキストのみを出力してください。元のテキストの核心的な意味は維持してください。

元のテキスト:
---
{text}
---

平易な表現:""",
    "add_term_explanation": """以下のテキストに含まれる可能性のある専門用語や略語に対して、簡単な解説や言い換えを括弧書きなどで追加してください。元のテキストは可能な限り維持し、解説が必要と思われる箇所に追記する形で編集してください。編集後のテキスト全体のみを出力してください。

元のテキスト:
---
{text}
---

解説を追加したテキスト:""",

    # コンテンツ生成・拡張
    "generate_examples": """以下のテキストで述べられている抽象的な説明や主張に対して、内容を補強するための具体的な例をいくつか生成してください。生成された具体例のみを、箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で出力してください。

元のテキスト:
---
{text}
---

具体例 (Markdownリスト):""",
    "generate_title_ideas": """以下のテキストの内容を要約し、その内容を表す魅力的なタイトルまたはセクション見出しの候補を3～5個提案してください。提案は箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で、候補リストのみを出力してください。

元のテキスト:
---
{text}
---

タイトル/見出し案 (Markdownリスト):""",

    # 構造化・フォーマット
    "format_faq": """以下のテキストの内容を分析し、想定される質問とその回答という形式（FAQ形式）に再構成してください。質問と回答のペアを明確に示し、Markdown形式で出力してください。FAQ形式のテキストのみを出力してください。

元のテキスト:
---
{text}
---

FAQ形式 (Markdown):""",
    "format_pros_cons": """以下のテキストの内容を分析し、そのトピックに関する利点（Pros）と欠点（Cons）を抽出し、それぞれ箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で整理してください。Pros/ConsリストのテキストのみをMarkdown形式で出力してください。

元のテキスト:
---
{text}
---

Pros/Consリスト (Markdown):""",
    "format_table": """以下のテキストに含まれる情報を分析し、比較やリスト表示に適したMarkdown形式のテーブルに整理してください。

**指示:**
1.  テキスト内から、表形式で表現できそうな**項目と値のペア**や**繰り返されるデータ構造**を注意深く見つけ出してください。
2.  抽出した情報に基づいて、適切な**ヘッダー行**を決定してください。
3.  **ヘッダー行、区切り線 (`|---|---|...`)、そして抽出したデータを含む全てのデータ行**を生成し、**完全な**Markdownテーブルを作成してください。
4.  生成するMarkdownテーブルのテキスト**のみ**を出力し、他の説明や前置きは**絶対に含めないでください**。

**元のテキスト:**
---
{text}
---

**Markdownテーブル (ヘッダーとデータ行を含む):**""",
    "extract_action_items": """以下のテキストから、「誰が」「何を」「いつまでに」行うべきかといった具体的なアクションアイテム（タスク）を抽出し、箇条書き（マークダウン形式のリスト `- [ ] ` を使用）でリストアップしてください。抽出されたアクションアイテムのリストのみを出力してください。

元のテキスト:
---
{text}
---

アクションアイテム (Markdownリスト):""",

    # 分析・抽出
    "sentiment_analysis": """以下のテキストの内容を分析し、全体的な感情（ポジティブ、ネガティブ、ニュートラルなど）を判定してください。判定結果とその簡単な理由や、特に感情が現れている箇所を指摘してください。結果のみを簡潔に出力してください。

元のテキスト:
---
{text}
---

感情分析結果:""",
    "extract_main_points": """以下のテキストの主要な主張、論点、または最も重要なポイントを抽出し、箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）で簡潔にまとめてください。抽出された要点のリストのみを出力してください。

元のテキスト:
---
{text}
---

主要な主張/論点 (Markdownリスト):""",
    "extract_entities": """以下のテキストから、固有名詞（人名、地名、組織名、製品名、日付など）を抽出し、箇条書き（マークダウン形式のリスト `- ` や `* ` を使用）でリストアップしてください。抽出された固有名詞のリストのみを出力してください。

元のテキスト:
---
{text}
---

抽出された固有名詞 (Markdownリスト):""",
    "analyze_risks": """あなたは経験豊富なリスクアナリストです。以下のテキスト（プロジェクト計画、状況報告、会議メモなど）を注意深く読み、潜在的なリスク要因、問題点、懸念事項、または不確実性を特定してください。

**指示:**
1.  特定した各リスクについて、簡潔な説明を記述してください。
2.  可能であれば、そのリスクが顕在化した場合に考えられる影響についても触れてください。
3.  抽出したリスクとその分析結果を、マークダウン形式の箇条書きリストで出力してください。リストのみを出力し、他の説明や前置きは不要です。

**元のテキスト:**
---
{text}
---

**リスク分析結果 (Markdownリスト):**""",

    "analyze_survey": """あなたはデータアナリストです。以下のテキスト（アンケートの自由回答、顧客レビュー、フィードバックなど）を分析し、主要な意見や傾向を要約してください。

**指示:**
1.  テキスト全体から、頻繁に言及されるトピックや共通のテーマを特定してください。
2.  肯定的な意見と否定的な意見（または課題点）を分けて抽出・要約してください。
3.  具体的な改善提案や要望があれば、それらもまとめてください。
4.  分析結果を、マークダウン形式の箇条書きや小見出しを使って分かりやすく整理して出力してください。分析結果のテキストのみを出力し、他の説明や前置きは不要です。

**元のテキスト:**
---
{text}
---

**アンケート分析結果 (Markdown):**""",

    "translate_zh": """以下のテキストを、自然で正確なビジネスシーンにも適した中国語（簡体字を優先）に翻訳してください。翻訳後の中国語テキストのみを出力してください。他の説明や前置きは不要です。

元のテキスト:
---
{text}
---

中国語訳:""",
    "brainstorm_expand": """以下のテキストで述べられているアイデアやキーワードについて、さらに発想を広げるための関連キーワード、サブトピック、疑問点、連想される事柄などを自由にブレインストーミングし、マークダウンの箇条書きでリストアップしてください。できるだけ多様な視点から、多くの項目を挙げてください。リストのみを出力してください。

元のテキスト:
---
{text}
---

ブレインストーミング結果 (Markdownリスト):""",

    "categorize_ideas": """以下のリストやテキストに含まれる複数のアイデアや項目を分析し、それらをいくつかの適切なカテゴリに分類・整理してください。各カテゴリ名と、そのカテゴリに属する項目をマークダウンの見出しと箇条書きで示してください。分類結果のテキストのみを出力してください。

元のテキスト/リスト:
---
{text}
---

分類結果 (Markdown形式):
## カテゴリA
* アイデア1
* アイデア5
## カテゴリB
* アイデア2
* アイデア4
## その他
* アイデア3""",

    "prioritize_ideas": """以下のアイデアリストについて、一般的なビジネスシーンにおける重要度や実現可能性を考慮して、優先順位付けを行ってください。各アイデアに優先度（例: 高、中、低）と、その簡単な理由を付与し、マークダウンのリスト形式で示してください。優先度が高いものから順に並べてください。リストのみを出力してください。

元のアイデアリスト:
---
{text}
---

優先順位付け結果 (Markdownリスト):
- 高: [アイデアX] (理由: ...)
- 中: [アイデアY] (理由: ...)
- 低: [アイデアZ] (理由: ...)""",

    "generate_action_plan_from_idea": """以下のアイデアを実現するための、具体的なステップからなる簡単な行動計画を生成してください。各ステップは、マークダウンのチェックボックスリスト (`- [ ]`) 形式で、簡潔なタスクとして記述してください。計画リストのみを出力してください。

アイデア:
---
{text}
---

行動計画 (Markdownチェックボックスリスト):
- [ ] ステップ1: (具体的なタスク)
- [ ] ステップ2: (具体的なタスク)
- [ ] ステップ3: (具体的なタスク)""",

    "find_analogies_for_idea": """以下のアイデアや概念について、それをよりよく理解したり、新しい切り口を見つけるために役立つ可能性のある、他の分野や状況における類似の事例（アナロジー）をいくつか提案してください。提案はマークダウンの箇条書きで、各アナロジーとその簡単な説明を記述してください。アナロジーリストのみを出力してください。

元のアイデア/概念:
---
{text}
---

アナロジー提案 (Markdownリスト):
*   [アナロジー1]: (簡単な説明と、元のアイデアとの関連性)
*   [アナロジー2]: (簡単な説明と、元のアイデアとの関連性)""",

    "refine_problem_statement": """以下のテキストは、ある問題や課題について述べています。この問題定義を、より明確（曖昧さがないか）、具体的（誰のどんな問題か）、測定可能（解決したことをどう判断できるか）、達成可能（現実的か）、関連性（より大きな目標とどう繋がるか）、期限付き（いつまでに解決すべきか）といった観点（SMART原則などを参考に）から分析し、洗練された問題定義の案を提示してください。洗練後の問題定義案のテキストのみを出力してください。

元の問題記述:
---
{text}
---

洗練された問題定義案:""",
}


SELECTION_TASK_CONFIG = {
    "summarize": {"max_output_tokens": 8192, "temperature": 0.5},
    "rewrite": {"max_output_tokens": 8192, "temperature": 0.7},
    "elaborate": {"max_output_tokens": 8192, "temperature": 0.7},
    "counter_argument": {"max_output_tokens": 8192, "temperature": 0.5},
    "generate_questions": {"max_output_tokens": 8192, "temperature": 0.4},
    "extract_keywords": {"max_output_tokens": 8192, "temperature": 0.2},
    "suggest_structure_improvement": {"max_output_tokens": 8192, "temperature": 0.4},
    "translate_en": {"max_output_tokens": 8192, "temperature": 0.3},
    "translate_ja": {"max_output_tokens": 8192, "temperature": 0.3},
    "tone_polite": {"max_output_tokens": 8192, "temperature": 0.5},
    "audience_expert": {"max_output_tokens": 8192, "temperature": 0.6},
    "audience_beginner": {"max_output_tokens": 8192, "temperature": 0.7},
    "format_email": {"max_output_tokens": 8192, "temperature": 0.5},
    "format_minutes": {"max_output_tokens": 8192, "temperature": 0.4},
    "format_report": {"max_output_tokens": 8192, "temperature": 0.5},
    "format_markdown": {"max_output_tokens": 8192, "temperature": 0.4},
    "convert_to_list": {"max_output_tokens": 8192, "temperature": 0.3},
    "convert_to_paragraph": {"max_output_tokens": 8192, "temperature": 0.6},
    "tone_academic": {"max_output_tokens": 8192, "temperature": 0.4},
    "tone_emotional": {"max_output_tokens": 8192, "temperature": 0.8},
    "tone_objective": {"max_output_tokens": 8192, "temperature": 0.3},
    "simplify": {"max_output_tokens": 8192, "temperature": 0.5},
    "toggle_voice": {"max_output_tokens": 8192, "temperature": 0.6},
    "add_metaphor": {"max_output_tokens": 8192, "temperature": 0.7},
    "remove_metaphor": {"max_output_tokens": 8192, "temperature": 0.5},
    "add_term_explanation": {"max_output_tokens": 8192, "temperature": 0.5},
    "generate_examples": {"max_output_tokens": 8192, "temperature": 0.6}, # モーダル表示想定
    "generate_title_ideas": {"max_output_tokens": 8192, "temperature": 0.7}, # モーダル表示想定
    "format_faq": {"max_output_tokens": 8192, "temperature": 0.5},
    "format_pros_cons": {"max_output_tokens": 8192, "temperature": 0.4}, # モーダル表示想定
    "format_table": {"max_output_tokens": 8192, "temperature": 0.3},
    "extract_action_items": {"max_output_tokens": 8192, "temperature": 0.3}, # モーダル表示想定
    "sentiment_analysis": {"max_output_tokens": 8192, "temperature": 0.2}, # モーダル表示想定
    "extract_main_points": {"max_output_tokens": 8192, "temperature": 0.3}, # モーダル表示想定
    "extract_entities": {"max_output_tokens": 8192, "temperature": 0.2}, # モーダル表示想定
    "analyze_risks": {"max_output_tokens": 8192, "temperature": 0.5},    # モーダル表示推奨
    "analyze_survey": {"max_output_tokens": 8192, "temperature": 0.4},   # モーダル表示推奨
    "translate_zh": {"max_output_tokens": 8192, "temperature": 0.3},     # 直接挿入 or モーダル表示
    "brainstorm_expand": {"max_output_tokens": 8192, "temperature": 0.7}, # モーダル表示推奨
    "categorize_ideas": {"max_output_tokens": 8192, "temperature": 0.5},  # モーダル表示 or 直接挿入
    "prioritize_ideas": {"max_output_tokens": 8192, "temperature": 0.4},  # モーダル表示推奨
    "generate_action_plan_from_idea": {"max_output_tokens": 8192, "temperature": 0.6}, # 直接挿入 or モーダル表示
    "find_analogies_for_idea": {"max_output_tokens": 8192, "temperature": 0.7}, # モーダル表示推奨
    "refine_problem_statement": {"max_output_tokens": 8192, "temperature": 0.5}, # 直接挿入
}

DIAGRAM_PROMPT_TEMPLATES = {
    "svg": """
## 指示: 高品質でモダンなSVG図解の生成

与えられたテキスト内容を分析し、**プロフェッショナルで視覚的に洗練されたSVG図解**を生成してください。

### 【最重要・絶対厳守】出力形式
- あなたの応答は、**`<svg` タグで始まり `</svg>` タグで終わる、完全なSVGコードの文字列そのもの**でなければなりません。
- **解説、前置き、後書き、Markdownのコードブロック囲み文字（例: ```xml や ```svg）など、SVGコード以外のテキストは絶対に含めないでください。**
- 応答の1文字目が必ず `<` になるようにしてください。

### デザインとレイアウトの重要原則
1.  **可読性 (Readability):**
    *   **色のコントラスト:** テキストと背景色には十分なコントラストを確保してください。**背景色と文字色が同じにならないように、常に確認してください。**
    *   **フォント:** フォントサイズは読みやすい大きさ（例: `14px` 以上）を基本とし、`font-family="system-ui, sans-serif"` のようなモダンなフォントを指定してください。
2.  **構造と整理 (Structure & Organization):**
    *   **要素の配置:** 関連する要素はグループ化し、視覚的に近接配置してください。要素同士が重ならないように、十分なマージン（余白）を確保してください。
    *   **グリッドと整列:** 目に見えないグリッドを意識し、要素の上端・下端・中心を揃えることで、整然とした印象を与えてください。
3.  **関係線の配線 (Connections):**
    *   **線の重なり回避:** **矢印や関係線が、他の図形やテキストの上に重なって見づらくなることを絶対に避けてください。** 線は図形の外側を通るように、始点と終点の座標を慎重に計算してください。
4.  **視覚的魅力 (Aesthetics):**
    *   **配色:** 調和のとれたカラーパレット（2〜4色程度）を使用してください。
    *   **スタイル:** `filter: drop-shadow(...)` を使った自然な影、`<linearGradient>` を使ったグラデーション背景、角丸 (`rx`, `ry`) などを効果的に使用し、リッチな表現を目指してください。

### 元のテキスト:
---
{text}
---

### 生成するSVGコード (必ず`<svg ...>`から開始):
""",
    "mermaid": """
## 指示: 高品質でスタイル付きのMermaid図解の生成 (v10対応)

与えられたテキスト内容を分析し、**視覚的に整理され、モダンで見やすいMermaid図解**を生成してください。
最も適切な図解の種類（`flowchart`, `sequenceDiagram`, `gantt`など）を自動で判断し、そのMermaidコードを出力してください。

### デザインとレイアウトの重要原則

1.  **ノード定義の厳格化:**
    *   **IDとラベルの分離:** ノードIDは簡潔な英数字（例: `id1`, `user`, `db`）とし、表示するテキストは必ず `"`（二重引用符）で囲んでください。例: `id1["表示テキスト"]`
    *   **可読性:** ノード内のテキストは簡潔にし、必要であれば `br` タグで改行してください。

2.  **関係線の整理:**
    *   **線の重なり回避:** Mermaidは自動でレイアウトしますが、複雑な図では線が重なることがあります。その場合、**サブグラフ（`subgraph`）**を使用して関連要素をグループ化し、レイアウトを整理してください。これにより、Mermaidのレンダリングエンジンがより良い配線を計算しやすくなります。
    *   **線の種類:** `-->` (実線), `--->` (ラベル付き実線), `-.->` (破線), `-. text .->` (ラベル付き破線) などを使い分け、関係性の違いを表現してください。

3.  **スタイルとリッチな表現:**
    *   **`classDef` と `class` の活用:** `classDef` を使ってノードのスタイル（背景色、文字色、枠線の色・太さなど）を定義し、ノードに `:::` でクラスを適用してください。
    *   **色のコントラスト:** **背景色と文字色が同じにならないように、`fill` と `color` の組み合わせには細心の注意を払ってください。**
    *   **FontAwesomeアイコン:** `fa:fa-icon-name` の形式で、ノードのテキストにアイコンを追加し、視覚的な理解を助けてください。例: `db["fa:fa-database データベース"]`
    *   **テーマ:** 図全体の雰囲気を設定するために、先頭に `%%{{init: {{'theme': 'base', 'themeVariables': {{'primaryColor': '#A6E3A1', 'lineColor': '#6E7387'}}}}}}%%` のようなテーマ設定ディレクティブを使用することを検討してください。

### 元のテキスト:
---
{text}
---

## 生成するMermaidコード (```mermaid ... ```のコードブロック形式で出力):
""",
    # --- Markmap, Graphviz, PlantUML のプロンプトは、元のままでも十分機能するため、ここでは変更しません。 ---
    # --- SVGとMermaidの改善に注力します。 ---
    "markmap": """
## 指示: Markmap用Markdown生成 (絵文字活用)
以下のテキストの内容を、Markmapライブラリで**視覚的に分かりやすい**マインドマップ風に表示できる階層構造を持つMarkdownテキストに変換してください。
項目名の先頭に、内容を表す適切な絵文字 ✨ 💡 📊 🚀 ✅ 🎯 などを積極的に使用し、視認性を高めてください。
生成するのはMarkmap用のMarkdownテキストのみとし、前後に説明文や ```markmap 等のマークダウンは**絶対に含めないでください**。

## 元のテキスト:
---
{text}
---

## 生成するMarkmap用Markdownテキスト:
""",
    "graphviz": """
## 指示: 高品質なGraphviz (DOT言語) コード生成
以下のテキストの内容を、**Graphviz (DOT言語)** を使って、構造化された分かりやすい図として表現してください。
`digraph G {{ ... }}` の形式で、ノードの形状(shape)、スタイル(style)、色(color)、エッジのスタイルなどを活用して、モダンで見やすい図を生成してください。
生成するのはDOT言語のコードのみとし、前後に説明文や ```graphviz 等のマークダウンは**絶対に含めないでください**。

## 元のテキスト:
---
{text}
---

## 生成するGraphviz (DOT言語) コード:
""",
    "d2": """
## 指示: 高品質なD2 (Declarative Diagramming) コード生成
以下のテキストの内容を、**D2言語** を使って、構造化された分かりやすい図として表現してください。
あなたのタスクは、後述の厳格な思考プロセスと構文ルールに従い、モダンで見やすい図のコードを生成することです。

### 【最重要】思考プロセスと構文ルール

**ステップ1: 思考 - ノードの属性を分類する**
まず、各ノード（図の要素）について、その属性を以下の2種類に頭の中で分類してください。
1.  **トップレベル属性:** ノードの基本的な定義。
    - `label`: ノードに表示するテキスト。
    - `shape`: ノードの形状 (例: `rectangle`, `circle`, `cylinder`)。
2.  **スタイル属性:** ノードの見た目に関する定義。
    - `fill`: 背景色。
    - `stroke`: 枠線の色。
    - `stroke-width`: 枠線の太さ。
    - `stroke-dash`: 破線 (値は整数)。
    - `bold`: テキストの太字 (値は `true` / `false`)。
    - `font-size`: フォントサイズ (値は整数)。
    - `shadow`: 影の有無 (値は `true` / `false`)。

**ステップ2: 実行 - 厳格な構文でコードを記述する**
分類した属性を、以下の厳格なルールに従ってコード化してください。
- **トップレベル属性 (`label`, `shape`) は、必ず `{{}}` の直下に記述します。**
- **スタイル属性 (`fill`, `bold` など) は、必ず `style: {{{{ ... }}}}` ブロックの**中に**記述します。**
- 接続 (矢印) に複数の属性 (`label` と `style` など) を指定する場合、必ず**セミコロン (`;`)** で区切ってください。

#### 具体例
- **(正しい例)**
  ```d2
  my_node: {{
    label: "これが正しいラベル"
    shape: cylinder
    style: {{{{
      fill: "#87CEEB"
      bold: true
      font-size: 16
    }}}}
  }}

  my_node -> another_node: {{
    label: "正しい接続"
    style: {{{{
      stroke-dash: 4
    }}}}
  }}
  ```

- **(絶対に避けるべき誤った例)**
  ```d2
  # fill, stroke, shape が style ブロックの外にあるためエラー
  my_node: {{
    fill: "#87CEEB"
    stroke: "#1E90FF"
    shape: cylinder
  }}

  # 接続属性がカンマ区切りになっているためエラー
  my_node -> another_node: {{{{ label: "間違い", style: {{{{ ... }}}} }}}}
  ```

### 出力指示
上記のルールを**絶対に遵守**し、D2言語のコードのみを出力してください。前後に説明文や ```d2 等のマークダウンは**絶対に含めないでください**。

### 元のテキスト:
---
{text}
---

### 生成するD2 (Declarative Diagramming) コード:
""",
    "plantuml": """
## 指示: 高品質なPlantUMLコード生成
以下のテキストの内容を、**PlantUML** を使って、適切な図（シーケンス図、クラス図、ユースケース図、アクティビティ図など）として表現してください。
`@startuml ... @enduml` の形式で、モダンで分かりやすい図を生成してください。
生成するのはPlantUMLのコードのみとし、前後に説明文や ```plantuml 等のマークダウンは**絶対に含めないでください**。

## 元のテキスト:
---
{text}
---

## 生成するPlantUMLコード:
""",
    "html": """
## 指示: モダンなHTML/CSS図解生成
以下のテキストの内容を、**視覚的に整理され、モダンで見やすいHTMLとCSS**で表現するコード片を生成してください。
生成するのは図解を表すHTMLコードの断片（例: `<div class="diagram">...</div>`）のみとし、`<html>`, `<head>`, `<body>` タグや ```html 等のマークダウンは絶対に含めないでください。

## 元のテキスト:
---
{text}
---

## 生成するHTMLコード (例: `<div class="diagram-container"><style>/* CSS推奨 */</style>...</div>`):
""",
    "system_instruction_mermaid": r"""
        ## Mermaid 生成指示
        *  `tool_code(...)` のコードブロックの生成は避けてください。

        1.  **コードブロック:**
            *    ```mermaid ... ``` で囲む。

        2.  **図の種類:**
            *   フローチャート (`graph LR`/`graph TD`), シーケンス図 (`sequenceDiagram`) などを明確にする。

        3.  **日本語:**
            *   ラベル、テキスト、コメント、全角スペースは `""` で囲む。

        4.  **記号:**
            *   Mermaid 構文で特別な意味を持つ記号は避ける。
                *   **避ける:** `-->`, `---`, `==>`, `<==>`, `&`, `#`, `--`, `==`, `_`, `*`, `|`, `:`, `;`, `,`, `()`, `[]`, `{}`, `(())`, `[[]]`, `{{}}`, `[()]`, `[( )]`, `[/ /]`, `[\\ \]`, `> ]`
                *   **代替表現例:** `-->`: 「から」、`---`: 「実線」、`&`: 「アンド」 or HTML エンティティ
            *   他の記号: HTML エンティティ (例: `<`: `& # 6 0 ;` (空白削除))

        5.  **英数字:**
            *   ノード ID、クラス名、エッジラベルは英数字推奨。

        6.  **コメント:**
            *   `%%` を使用。

        7.  **詳細:**
            *   Mermaid 公式ドキュメント参照。
            *   Mermaidコードを生成する際は、以下のルールを**厳守**してください。
                *   **図の種類:** 生成する図の種類（フローチャート、シーケンス図、ガントチャートなど）を明確に指示します。フローチャートの場合は、`graph LR` または `graph TD` から始めてください。
                *   **日本語の扱い:**
                    *   日本語の**ラベル、テキスト、コメント**、**全角スペース**は必ず `""` で囲ってください。
                    *   クラス名、ID名、および一部のキーワード（例: `graph LR`, `subgraph`, `end`）内では、日本語を `""` で囲む必要はありません。
                *   **記号の禁止と代替表現:**
                    *   Mermaid構文で特別な意味を持つ可能性のある記号は、可能な限り使用しないでください。特に、以下の記号は使用を避けてください。
                        *   `-->`, `---`, `==>`, `<==>` (矢印)
                        *   `&`, `#` (コメントアウトに使用される場合があるため)
                        *   `--`, `==`
                        *   `_`, `*` (テキスト修飾関連)
                        *   `|` (テーブルで使用)
                        *   `:`, `;`, `,`
                        *   `()`, `[]`, `{}`, `(())`, `[[]]`, `{{}}`, `[()]`, `[( )]`, `[/ /]`, `[\\ \]`, `> ]`
                    *   これらの記号を表現する必要がある場合は、以下の代替表現を使用してください。
                        *   `-->`: 「から」
                        *   `---`: 「実線」
                        *   `==>`: 「太い矢印から」
                        *   `<==>`: 「太い矢印双方向」
                        *   `&`: 「アンド」または `& # 3 8 ;` (空白は削除してください)
                        *   `#`: 「シャープ」または「番号」または `& # 3 5 ;` (空白は削除してください)
                        *   `--`: 「二つのハイフン」
                        *   `==`: 「二つのイコール」
                        *   `_`: 「アンダースコア」
                        *   `*`: 「アスタリスク」
                        *   `|`: 「パイプ」または「縦線」
                        *   `:`: 「コロン」
                        *   `;`: 「セミコロン」
                        *   `,`: 「カンマ」
                        *   `()`: 「丸括弧」「括弧」「パーレン」
                        *   `[]`: 「角括弧」「ブラケット」
                        *   `{}`: 「波括弧」「ブレース」
                    *   上記以外の記号で、使用する必要がある場合は、HTMLエンティティを使用して表現してください。（例：`& # 6 0 ;` (空白は削除してください) は `<` を表します）
                *   **英数字の使用:**
                    *   ノードID、クラス名、エッジラベルには、可能な限り英数字を使用してください。
                *   **コードブロック:**
                    *   生成されたMermaidコードは、\`\`\`mermaid ... \`\`\` で囲んでください。
                *   **コメント:**
                    *   必要に応じて、`%%` を使用してMermaidコード内にコメントを追加してください。
                *   **重要:**
                    *   上記指示は、Mermaidコードの構文エラーを防ぐことを目的としています。
                    *   指示に違反した場合、Mermaidコードが正しくレンダリングされない可能性があります。
                    *   生成されたコードは、生成後に必ず確認し、必要に応じて修正してください。
                    *   これらの指示は、Mermaidのバージョンによって異なる可能性があるため、最新のMermaid公式ドキュメントを参照してください。

                * **生成するMermaidコードのダイアグラムの種類に応じて、以下の点に留意してください。**
                    ### 1. Flowchart (フローチャート)

                    *   フローチャートを生成する場合は、`graph LR` または `graph TD` から始めてください。
                    *   各ステップは適切な形状のノード（四角形、角丸四角形、菱形など）で表してください。
                    *   条件分岐は菱形ノードを使用し、分岐の条件を明確にラベル付けしてください。
                    *   処理の流れは矢印で接続してください。
                    *   複雑な場合は、`subgraph` を使用して関連するステップをグループ化してください。

                    ### 2. Sequence Diagram (シーケンス図)

                    *   シーケンス図を生成する場合は、`sequenceDiagram` から始めてください。
                    *   関係するアクターとオブジェクトを明確に定義してください。
                    *   メッセージの種類（同期、非同期、応答）を明確にし、適切な矢印で表現してください。
                        *   同期メッセージ: `->>`
                        *   非同期メッセージ: `->`
                        *   応答メッセージ: `-->>`
                    *   必要に応じて、`loop`, `alt`, `opt`, `par` を使用して、繰り返し、条件分岐、オプション、並列処理を表現してください。
                    *   オブジェクトの生存期間をアクティベーションバー（`activate`, `deactivate`）で示してください。

                    ### 4. Entity Relationship Diagram (ER図)

                    *   ER図を生成する場合は、`erDiagram` から始めてください。
                    *   エンティティ、属性、関係を明確に定義してください。
                    *   各エンティティの属性は、`属性の型 属性名` の形式で記述してください。
                    *   関係の種類（識別/非識別）とカーディナリティを、以下の記号の組み合わせで表現してください。
                        *   識別関係: 実線 (`-`) または 点線 (`.`)
                        *   カーディナリティ: `||` (1対1), `|o` (0または1対1), `|{` (1対多), `o{` (0または1対多)
                    *   関係は、`エンティティ1 関係記号 エンティティ2 : 関係ラベル` の形式で記述してください。

                    ### 5. User Journey (ユーザージャーニー)

                    *   ユーザージャーニーマップを生成する場合は、`journey` から始めてください。
                    *   ユーザーの行動、タッチポイント、感情、思考などを時系列で記述してください。
                    *   `section` を使用して、情報を整理してください。（例：調査、検討、購入、利用）
                    *   各ステップでのユーザーの感情を、:(happy):, :(sad):, :(neutral): などで表現してください。

                    ### 6. Gantt (ガントチャート)

                    *   ガントチャートを生成する場合は、`gantt` から始めてください。
                    *   `dateFormat YYYY-MM-DD` で日付の形式を指定してください。
                    *   `title` でチャートのタイトルを設定してください。
                    *   `section` を使用して、タスクをグループ化してください。
                    *   各タスクを `タスク名 :[ステータス],[開始日],[終了日]` または `タスク名 :[ステータス],[開始日],[期間]` の形式で記述してください。
                        *    ステータスは、`done`, `active`, `crit` など。
                    *   タスク間の依存関係を `after タスク名` で表現してください。
                    *   マイルストーンを `milestone :[ステータス],[日付]` で表現してください。

                    ### 7. Pie Chart (円グラフ)

                    *   円グラフを生成する場合は、`pie` から始めてください。
                    *   `title` でグラフのタイトルを設定してください。
                    *   各項目のラベルと値を `"ラベル" : 値` の形式で記述してください。
                    *   値の合計が100になるように調整してください。

                    ### 8. Quadrant Chart (象限チャート)

                        1.  **象限チャートの基本構造:**

                            *   象限チャートは `quadrantChart` で始めます。
                            *   以下の要素で構成されます。
                                *   `title`: チャートのタイトル (省略可)。
                                *   `x-axis`: X軸の定義。
                                *   `y-axis`: Y軸の定義。
                                *   `quadrant-1`, `quadrant-2`, `quadrant-3`, `quadrant-4`: 各象限のラベル。
                                *   データポイント: 各点をプロット。

                        2.  **タイトルの設定 (省略可):**

                            *   `title "タイトル文字列"` の形式で設定します。
                            *   **日本語を含む場合は必ず `""` で囲んでください。**

                        3.  **X軸とY軸の設定:**

                            *   `x-axis "最小ラベル" --> "最大ラベル"` の形式でX軸のラベルを設定します。
                            *   `y-axis "最小ラベル" --> "最大ラベル"` の形式でY軸のラベルを設定します。
                            *  **日本語を含む場合は必ず `""` で囲んでください。**
                            *   混乱を避けるため、現状では、`x-axis`,`y-axis`ともに、最小ラベルのみ、または最小ラベルと最大ラベルの両方を記述してください。最大ラベルのみの記述は避けてください。

                        4.  **象限のラベル設定:**

                            *   `quadrant-1 "ラベル文字列"`
                            *   `quadrant-2 "ラベル文字列"`
                            *   `quadrant-3 "ラベル文字列"`
                            *   `quadrant-4 "ラベル文字列"`
                            *   の形式で、各象限のラベルを設定します。
                            *   **日本語を含む場合は必ず `""` で囲んでください。**

                        5.  **データポイントの定義:**

                            *   データポイントは `"データポイント名": [x値, y値]` の形式で記述します。
                                *   `"データポイント名"`: 任意の文字列 (**日本語を含む場合は必ず `""` で囲む**)
                                *   `x値`, `y値`: 0から1の間の数値。
                            *   オプション (以下の属性を追加可能):
                                *   `radius: 数値` (点の半径)
                                *   `color: #hex` または `color: 色名` (点の色)
                                *   `stroke-color: #hex` または `stroke-color: 色名` (点の枠線の色)
                                *   `stroke-width: 数値px` (点の枠線の太さ)
                                *   `:::クラス名` (定義済みのクラスを適用)

                        6.  **クラスの定義 (オプション):**

                            *   `classDef クラス名 スタイル` の形式でクラスを定義します。
                            *   `スタイル` は、CSSスタイルをカンマ区切りで記述 (例: `color:#ff0000,stroke-width:2px`)

                        7.  **その他:**
                            *   x値, y値 は 0から1の間の数値で指定してください

                    ### 11. C4 Diagram

                    *   C4ダイアグラムを生成する場合は、`C4Context`, `C4Container`, `C4Component` のいずれかから始めてください。
                    *   各要素のタイプ（Person, System, Container, Component）、技術、説明を記述してください。
                    *   `Rel(source, destination, label, technology)` を使用して、要素間の関係を記述してください。

                    ### 12. Timeline

                    *   タイムライン図を生成する場合は、`timeline` から始めてください。
                    *   `title` でタイムラインのタイトルを設定してください。
                    *   `section セクション名` でイベントをグループ化できます。
                    *   各イベントを `期間 : イベントタイトル : イベント詳細` の形式で記述してください。期間は、単一の日付または日付範囲 (YYYY-MM-DD - YYYY-MM-DD) で指定します。

            


    """,
    "system_instruction_plantuml": f"""
        ## PlantUML 生成指示
        *  `tool_code(...)` のコードブロックの生成は避けてください。

        1.  **コードブロック:**
            *   ```plantuml ... ``` で囲む。

        2.  **バージョン:**
            *   PlantUML {PLANTUML_VERSION} 以降の規約に従う。

        3.  **記号:**
            *   構文エラー回避のため、特別な意味を持つ記号は原則使用禁止。
                *   **禁止:** `-->`, `---`, `==>`, `<==>`, `&`, `#`, `--`, `==`, `_`, `*`, `|`, `:`, `;`, `,`, `()`, `[]`, `{{}}`, `(())`, `[[]]`, `{{{{}}}}`, `[()]`, `[( )]`, `[/ /]`, `[\\\\ \\]`, `> ]`
                *   **代替:** 言葉で表現。PlantUML コメント (`/' コメント '/`) を使用。

        4.  **日本語ラベル:**
            *   オブジェクト/参加者名はそのまま記述 (例: `participant 参加者A`)。

        5.  **条件分岐:**
            *   シーケンス図で `alt` は使用可。複雑な分岐は避ける。

        6.  **複数ダイアグラム:**
            *   `@startuml` で開始、`@enduml` で終了。

        7.  **詳細:**
            *   PlantUML 公式サイト参照。
            * PlantUMLコードを生成する際は、以下のルールを**厳守**してください。
                * PlantUML バージョン {PLANTUML_VERSION} 以降のコーディング規約に基づいて生成してください。
                * **PlantUMLの構文エラーを避けるため、以下の点に注意してください。**
                    * **記号の使用制限:** 以下の記号は、PlantUMLの構文で特別な意味を持つため、原則として使用を避けてください。
                        * 矢印: `-->`, `---`, `==>`, `<==>` (代わりに、「から」「へ」のような言葉で関係性を表現してください。例: `A --> B` は `A から B`)
                        * コメントアウト関連: `&`, `#` (コメントが必要な場合は、PlantUMLのコメント構文 `/' コメント '/` を使用してください)
                        * テキスト修飾: `--`, `==`, `_`, `*` (太字や斜体などの表現は、PlantUMLの構文でサポートされている範囲で使用してください)
                        * テーブル関連: `|` (テーブルの表現が必要な場合は、PlantUMLのテーブル構文を使用してください)
                        * その他の記号: `:`, `;`, `,`, `()`, `[]`, `{{}}`, `(())`, `[[]]`, `{{{{}}}}`, `[()]`, `[( )]`, `[/ /]`, `[\\\\ \\]`, `> ]` (これらの記号の代わりに言葉で表現するか、PlantUMLの構文で許容される代替表現を使用してください)
                    * **日本語ラベル:** オブジェクトや参加者などの要素に日本語名を表示する場合は、そのまま記述してください。（例：`participant 参加者A`）
                    * **条件分岐:** シーケンス図で条件分岐を表現する場合は `alt` を使用できますが、複雑な条件分岐（`alt` のネストなど）は避けてください。
                * **複数のPlantUMLダイアグラムを生成する場合:** 各ダイアグラムは `@startuml` で開始し、`@enduml` で終了してください。
                ```plantuml
                @startuml
                ' 最初のダイアグラム
                ...
                @enduml

                @startuml
                ' 2番目のダイアグラム
                ...
                @enduml
                ```
                * **生成するPlantUMLのダイアグラムの種類に応じて、以下の点に留意してください。**
                    * **シーケンス図:**
                        * シーケンス図の目的は、オブジェクト間のメッセージのやり取りを時系列で表現します。
                        * 参加者間のメッセージのやり取りを表現します。
                        * participant は、オブジェクトを表す。
                        * メッセージは、オブジェクト間の処理の呼び出しを表す。具体的なコードではなく、処理の内容を記述する。
                        * loop、alt などの制御構造を使って、処理の流れを表現する。
                        * activate、deactivate は、オブジェクトの生存期間を表現する。
                        * `->`, `-->` などの矢印は、「から」「へ」のような言葉で表現してください。
                        * メッセージに同期（`->`）と非同期（`->>`）の区別がある場合は、「送信」「応答」などの言葉で区別を表現してください。
                        * `activate`、`deactivate` を使用して、参加者の活性化状態を表現できます。
                        * `alt`/`else`、`opt`、`loop` などを使用して、条件分岐や繰り返しを表現できますが、複雑な構造は避けてください。
                    * **ユースケース図:**
                        * アクターとユースケースの関係を表現します。
                        * アクターは `actor` キーワードで定義します。
                        * ユースケースは楕円で表現し、ユースケース名を記述します。
                        * アクターとユースケースの関係は線で結びます。
                        * `include` や `extend` を使用して、ユースケース間の関係を表現できます。
                    * **クラス図:**
                        * クラス、インターフェース、列挙型などの要素と、それらの間の関係（汎化、実現、関連、集約、コンポジション）を表現します。
                        * クラスは `class` キーワードで定義します。
                        * インターフェースは `interface` キーワードで定義します。
                        * 列挙型は `enum` キーワードで定義します。
                        * 属性は `attributeName : attributeType` の形式で記述します。
                        * 操作（メソッド）は `operationName(parameter1 : parameterType1, ...) : returnType` の形式で記述します。
                        * 関係の種類（汎化、実現、関連、集約、コンポジション）は、線の種類や矢印の形状で表現します。言葉で補足説明を追加することも可能です。
                    * **アクティビティ図:**
                        * 処理の流れ（ワークフロー）を表現します。
                        * 開始と終了は、それぞれ黒丸 `(*)` と二重丸で表現します。
                        * アクション（処理）は角丸の四角形で表現し、`:処理内容;` のようにコロンで囲んで記述します。
                        * フロー（矢印）でアクションの実行順序を示します。
                        * 分岐（ダイアモンド）を使用して、条件による処理の分岐を表現できます。条件分岐は以下のように記述します。
                            ```
                            if (条件) then (yes)
                            ...
                            else (no)
                            ...
                            endif
                            ```
                        * フォークとジョインを使用して、並行処理を表現できます。
                        * パーティション（スイムレーン）を使用して、処理の担当者や部署を表現できます。
                        * note は `note right: 説明文` の形式で、処理の右側に説明を付加できます。
                        * `goto` は使用しないでください。条件分岐やループを使って処理の流れを表現してください。
                    * **コンポーネント図:**
                        * システムの構成要素（コンポーネント）と、それらの間の依存関係を表現します。
                        * コンポーネントは `component` キーワードで定義します。
                        * インターフェースは `interface` キーワードで定義し、ロリポップ（丸）やソケット（半円）で表現します。
                        * コンポーネント間の依存関係は、点線矢印で表現します。
                    * **状態遷移図:**
                        * オブジェクトの状態遷移を表現します。
                        * 状態は `state 状態名 {{ ... }}` のように定義します。長い状態名の場合は `as` で別名を付けることができます。
                        * 開始状態は `[*]`、終了状態も `[*]` で表現します。
                        * 状態間の遷移は `状態A --> 状態B : ラベル` のように記述します。
                        * `entry` アクションは、状態に入ったときに実行されるアクションを定義します。`entry : アクション` のように記述します。
                        * 複合状態 (状態の中に状態を持つ) を表現できますが、状態のネストは深くなりすぎないように注意してください。
                        * 複合状態内の詳細な遷移は、`note right` (または `note left`) を使って複合状態の内部に記述することで、可読性を高めることができます。複合状態内部に詳細な状態遷移は作らないようにしてください。
                        * `entry` アクションは、状態に入ったときに実行されるアクションを定義します。`entry: アクション1, アクション2, ...` のように、複数のアクションをカンマで区切って記述できます。
                        * **複合状態間の遷移は、複合状態自体を結びつけるように記述してください。複合状態の内部の状態を直接結びつけることはできません。**
                    * **オブジェクト図:**
                        * 特定の時点におけるオブジェクトのインスタンスと、それらの間の関係を表現します。
                        * オブジェクトは `object` キーワードで定義し、`オブジェクト名 : クラス名` の形式で記述します。(クラス名は省略可能です)
                        * オブジェクト間の関係は線で結び、リンクの役割名(関連名)を記述できます。（例：`objectA -- objectB : 関連`）
                        * オブジェクト名は日本語で記述できます。
                        * **オブジェクト図の生成を指示する際は、アクティビティ図と混同しないように注意してください。`if`、`then`、`else`、`endif`、`(*)` などのアクティビティ図の要素は使用しないでください。**
                        * オブジェクト間の関係性として、汎化(<|--)、関連(--)、依存関係(<..)が使用できます。
                    * **配置図:**
                        * 物理的な要素（ノード、デバイス）と、それらに配置されるコンポーネントや成果物を表現します。
                        * ノードは `node` キーワードで定義します。
                        * デバイスは `device` キーワードで定義します。
                        * コンポーネントや成果物は、ノードの中に配置します。
                        * ノード間の接続は線で表現し、通信プロトコルなどを記述できます。
                * **PlantUMLの構文に関する詳細は、[PlantUML公式サイト](https://plantuml.com/ja/)を参照してください。**            
    """,
    "system_instruction_svg": """
        ## SVG(XML) 生成指示
        *  `tool_code(...)` のコードブロックの生成は避けてください。
        *  先頭にコードブロック

        1.  **コードブロック:**
                ```xml ... ```で囲む。
                先頭はコードブロック「```xml ... ```」以外の生成をしてください。先頭がコードブロックの場合エラーとなるため。

        2.  **例:** 
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600">
                <!-- 背景 -->
                <rect width="800" height="600" fill="#f8f9fa"/>

                <!-- タイトル -->
                <text x="400" y="40" font-family="Arial, sans-serif" font-size="24" text-anchor="middle" font-weight="bold">機械学習アルゴリズムの分類</text>

                <!-- 主要カテゴリー -->
                <g>
                    <!-- 教師あり学習 -->
                    <rect x="100" y="80" width="180" height="50" rx="10" fill="#4285f4" />
                    <text x="190" y="110" font-family="Arial, sans-serif" font-size="16" fill="white" text-anchor="middle">教師あり学習</text>

                    <!-- 教師なし学習 -->
                    <rect x="310" y="80" width="180" height="50" rx="10" fill="#34a853" />
                    <text x="400" y="110" font-family="Arial, sans-serif" font-size="16" fill="white" text-anchor="middle">教師なし学習</text>

                    <!-- 強化学習 -->
                    <rect x="520" y="80" width="180" height="50" rx="10" fill="#ea4335" />
                    <text x="610" y="110" font-family="Arial, sans-serif" font-size="16" fill="white" text-anchor="middle">強化学習</text>
                </g>

                <!-- 教師あり学習のアルゴリズム -->
                <g>
                    <!-- 線 -->
                    <line x1="190" y1="130" x2="190" y2="150" stroke="#4285f4" stroke-width="2"/>

                    <!-- 回帰 -->
                    <rect x="50" y="150" width="130" height="40" rx="8" fill="#cfe2ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="115" y="175" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">回帰</text>

                    <!-- 分類 -->
                    <rect x="200" y="150" width="130" height="40" rx="8" fill="#cfe2ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="265" y="175" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">分類</text>

                    <!-- 線 -->
                    <line x1="115" y1="190" x2="115" y2="210" stroke="#4285f4" stroke-width="1"/>
                    <line x1="265" y1="190" x2="265" y2="210" stroke="#4285f4" stroke-width="1"/>

                    <!-- 回帰アルゴリズム -->
                    <rect x="20" y="210" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="70" y="232" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">線形回帰</text>

                    <rect x="20" y="255" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="70" y="277" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">決定木回帰</text>

                    <rect x="20" y="300" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="70" y="322" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">ランダムフォレスト</text>

                    <rect x="20" y="345" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="70" y="367" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">ニューラルネット</text>

                    <!-- 分類アルゴリズム -->
                    <rect x="130" y="210" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="180" y="232" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">ロジスティック回帰</text>

                    <rect x="130" y="255" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="180" y="277" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">SVM</text>

                    <rect x="130" y="300" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="180" y="322" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">決定木分類</text>

                    <rect x="130" y="345" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="180" y="367" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">k近傍法</text>

                    <rect x="240" y="210" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="290" y="232" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">ナイーブベイズ</text>

                    <rect x="240" y="255" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="290" y="277" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">CNNs</text>

                    <rect x="240" y="300" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="290" y="322" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">RNNs</text>

                    <rect x="240" y="345" width="100" height="35" rx="5" fill="#e6f0ff" stroke="#4285f4" stroke-width="1"/>
                    <text x="290" y="367" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">Transformers</text>

                    <!-- 線 -->
                    <line x1="265" y1="190" x2="290" y2="210" stroke="#4285f4" stroke-width="1"/>
                </g>

                <!-- 教師なし学習のアルゴリズム -->
                <g>
                    <!-- 線 -->
                    <line x1="400" y1="130" x2="400" y2="150" stroke="#34a853" stroke-width="2"/>

                    <!-- クラスタリング -->
                    <rect x="310" y="150" width="130" height="40" rx="8" fill="#d4edda" stroke="#34a853" stroke-width="1"/>
                    <text x="375" y="175" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">クラスタリング</text>

                    <!-- 次元削減 -->
                    <rect x="460" y="150" width="130" height="40" rx="8" fill="#d4edda" stroke="#34a853" stroke-width="1"/>
                    <text x="525" y="175" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">次元削減</text>

                    <!-- 線 -->
                    <line x1="375" y1="190" x2="375" y2="210" stroke="#34a853" stroke-width="1"/>
                    <line x1="525" y1="190" x2="525" y2="210" stroke="#34a853" stroke-width="1"/>

                    <!-- クラスタリングアルゴリズム -->
                    <rect x="325" y="210" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="375" y="232" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">K-means</text>

                    <rect x="325" y="255" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="375" y="277" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">階層的クラスタリング</text>

                    <rect x="325" y="300" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="375" y="322" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">DBSCAN</text>

                    <rect x="325" y="345" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="375" y="367" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">GMM</text>

                    <!-- 次元削減アルゴリズム -->
                    <rect x="475" y="210" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="525" y="232" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">PCA</text>

                    <rect x="475" y="255" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="525" y="277" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">t-SNE</text>

                    <rect x="475" y="300" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="525" y="322" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">UMAP</text>

                    <rect x="475" y="345" width="100" height="35" rx="5" fill="#e8f5e9" stroke="#34a853" stroke-width="1"/>
                    <text x="525" y="367" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">オートエンコーダ</text>
                </g>

                <!-- 強化学習のアルゴリズム -->
                <g>
                    <!-- 線 -->
                    <line x1="610" y1="130" x2="610" y2="150" stroke="#ea4335" stroke-width="2"/>

                    <!-- 価値ベース -->
                    <rect x="560" y="150" width="130" height="40" rx="8" fill="#f8d7da" stroke="#ea4335" stroke-width="1"/>
                    <text x="625" y="175" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">価値ベース</text>

                    <!-- 方策ベース -->
                    <rect x="560" y="200" width="130" height="40" rx="8" fill="#f8d7da" stroke="#ea4335" stroke-width="1"/>
                    <text x="625" y="225" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">方策ベース</text>

                    <!-- モデルベース -->
                    <rect x="560" y="250" width="130" height="40" rx="8" fill="#f8d7da" stroke="#ea4335" stroke-width="1"/>
                    <text x="625" y="275" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">モデルベース</text>

                    <!-- 線 -->
                    <line x1="690" y1="170" x2="710" y2="170" stroke="#ea4335" stroke-width="1"/>
                    <line x1="690" y1="220" x2="710" y2="220" stroke="#ea4335" stroke-width="1"/>
                    <line x1="690" y1="270" x2="710" y2="270" stroke="#ea4335" stroke-width="1"/>

                    <!-- 価値ベースアルゴリズム -->
                    <rect x="710" y="152.5" width="70" height="35" rx="5" fill="#feebee" stroke="#ea4335" stroke-width="1"/>
                    <text x="745" y="174.5" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">Q-Learning</text>

                    <!-- 方策ベースアルゴリズム -->
                    <rect x="710" y="202.5" width="70" height="35" rx="5" fill="#feebee" stroke="#ea4335" stroke-width="1"/>
                    <text x="745" y="224.5" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">REINFORCE</text>

                    <!-- モデルベースアルゴリズム -->
                    <rect x="710" y="252.5" width="70" height="35" rx="5" fill="#feebee" stroke="#ea4335" stroke-width="1"/>
                    <text x="745" y="274.5" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">AlphaGo</text>
                </g>

                <!-- 特殊アルゴリズム -->
                <g>
                    <!-- アンサンブル学習 -->
                    <rect x="100" y="430" width="180" height="40" rx="8" fill="#fbbc04" />
                    <text x="190" y="455" font-family="Arial, sans-serif" font-size="14" fill="white" text-anchor="middle">アンサンブル学習</text>

                    <!-- 半教師あり学習 -->
                    <rect x="310" y="430" width="180" height="40" rx="8" fill="#9c27b0" />
                    <text x="400" y="455" font-family="Arial, sans-serif" font-size="14" fill="white" text-anchor="middle">半教師あり学習</text>

                    <!-- 転移学習 -->
                    <rect x="520" y="430" width="180" height="40" rx="8" fill="#03a9f4" />
                    <text x="610" y="455" font-family="Arial, sans-serif" font-size="14" fill="white" text-anchor="middle">転移学習</text>

                    <!-- 説明 -->
                    <rect x="100" y="480" width="180" height="80" rx="5" fill="#fff3e0" stroke="#fbbc04" stroke-width="1"/>
                    <text x="190" y="495" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">複数のモデルを組み合わせて</text>
                    <text x="190" y="510" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">精度を向上させる</text>
                    <text x="190" y="525" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Random Forest</text>
                    <text x="190" y="540" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Boosting (XGBoost, AdaBoost)</text>
                    <text x="190" y="555" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Stacking</text>

                    <rect x="310" y="480" width="180" height="80" rx="5" fill="#f3e5f5" stroke="#9c27b0" stroke-width="1"/>
                    <text x="400" y="495" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">少量のラベル付きデータと</text>
                    <text x="400" y="510" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">大量の未ラベルデータを使用</text>
                    <text x="400" y="525" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Self-training</text>
                    <text x="400" y="540" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Co-training</text>
                    <text x="400" y="555" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Pseudo-labeling</text>

                    <rect x="520" y="480" width="180" height="80" rx="5" fill="#e1f5fe" stroke="#03a9f4" stroke-width="1"/>
                    <text x="610" y="495" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">事前学習したモデルを</text>
                    <text x="610" y="510" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">新しいタスクに適用</text>
                    <text x="610" y="525" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Fine-tuning</text>
                    <text x="610" y="540" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Feature extraction</text>
                    <text x="610" y="555" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">・Domain adaptation</text>
                </g>
                </svg>

    """
}

DESIGN_CONCEPTS = {
    "auto": {
        "name": "任意（AIにおまかせ）",
        "description": "目的や内容からAIが最適なデザインを自動で選択します。"
    },
    "modern_corporate": {
        "name": "モダン・コーポレート",
        "description": "青やグレーを基調としたクリーンで信頼感のあるデザイン。ビジネスレポート、社内説明会に適しています。"
    },
    "bold_impact": {
        "name": "ボールド・インパクト",
        "description": "濃色の背景と鮮やかなアクセントカラーを使い、力強く印象的なデザイン。新製品発表やマーケティング提案に最適です。"
    },
    "elegant_minimalist": {
        "name": "エレガント・ミニマリスト",
        "description": "豊富な余白、洗練された書体、抑制された配色で、上品でミニマルなデザイン。ブランドストーリーやコンセプト提案に適しています。"
    },
    "vibrant_creative": {
        "name": "バイブラント＆クリエイティブ",
        "description": "明るい多色配色や遊び心のある図形を使い、創造的でエネルギッシュなデザイン。ブレインストーミングやワークショップに適しています。"
    },
    "data_driven_infographic": {
        "name": "データドリブン＆インフォグラフィック",
        "description": "情報を図やグラフとして視覚化することに重点を置いたデザイン。統計データの報告や、複雑な情報の図解に適しています。"
    }
}

ANIMATED_ICON_SETS_BLOCKLIST = [
    'line-md',          # Line MD Icons (Animated)
    'fad',              # Font Awesome Duotone (Animation capable)
    'icon-park',        # IconPark (Some are animated)
    'eos-icons',        # EOS Icons (Animated)
    'samherbert',       # Sam Herbert's Animated Icons
    'lucide',           # Lucide (Some animations available) - 念のため
]


# --- AI動画パフォーマンス分析機能 ---
VIDEO_ANALYSIS_MODEL_NAME = os.environ.get("VIDEO_ANALYSIS_MODEL_NAME") # 最新の高性能モデルを推奨

# AI動画分析のためのベースプロンプト
# {analysis_perspective} と {output_format_definition} はroutes.py側で動的に埋め込まれます
VIDEO_ANALYSIS_BASE_PROMPT = """
あなたは、映像、音声、会話内容を統合的に分析し、パフォーマンスの時間的変化まで捉えることができる、世界最高峰のビジネスパフォーマンスコーチです。
提供された会議動画とトランスクリプトを分析し、以下のタスクを厳格に実行してください。

## 【最重要】全体ルール
- **出力言語:** **すべての解説文、フィードバック、要約は、必ず日本語で生成してください。**
- **採点基準:**
  - `overall_score` および `time_series_analysis` のスコアは **0〜100点** の範囲で評価してください。
  - `performance_scores` (レーダーチャート用) の各項目は **0〜5点** の5段階評価で採点してください。

## タスク一覧
1.  **パフォーマンス評価:** 映像、音声、トランスクリプトを総合的に判断し、「分析観点」に従って会議全体と話者個人のパフォーマンスを評価してください。**「強み」と「改善点」については、それぞれ3～4文程度の詳細な解説を日本語で記述し、最も重要なキーワードをMarkdownの太字（**キーワード**）で囲んでください。**
2.  **時間軸分析 (Time-series Analysis):** 動画全体を**1分ごとのセグメント**に分割し、各セグメントにおけるパフォーマンス指標のスコア（0-100点）を評価してください。**スコアは単調にならないよう、パフォーマンスの変動をダイナミックに捉えてメリハリのある評価をしてください。**
3.  **フィードバック生成:** 各話者の良かった点（Good Point）と改善点（To Improve）を、タイムスタンプ付きで**日本語で**具体的に指摘してください。
4.  **トランスクリプト要約:** 提供されたトランスクリプトから、「あー」「えーと」などのフィラーワードや言い淀みを除去し、主要な発言のみを抽出した**日本語の要約版トランスクリプト**を生成してください。
5.  **JSON出力:** 全ての分析結果を、後述の「厳格なJSON出力形式」に完全に準拠した形で出力してください。

## 分析観点
{analysis_perspective}
{transcript_text_section}
## 厳格なJSON出力形式
あなたの応答は、以下の構造を持つ単一のJSONオブジェクト**のみ**でなければなりません。説明やマークダウン、```jsonの囲み文字は**絶対に含めないでください**。
{output_format_definition}
"""


# 各シナリオに応じた分析観点
VIDEO_ANALYSIS_PERSPECTIVES = {
    "facilitation": """
- **議題の進行:** アジェンダ通りに議論を進め、時間管理を適切に行えているか。
- **参加者の巻き込み:** 全員に均等な発言機会を与え、意見を引き出せているか。
- **議論の深化:** 議論が停滞した際に、的確な質問や要約で流れを作れているか。
- **合意形成:** 議論を収束させ、具体的な結論や次のアクションに繋げられているか。
- **雰囲気作り:** ポジティブで建設的な議論の場を作れているか。
""",
    "presentation": """
- **構成の論理性:** 序論・本論・結論の流れが明確で、聞き手を惹きつけられるか。
- **話し方:** 声のトーン、話す速度、明瞭さが適切で、聞きやすいか。
- **説得力:** 主張に一貫性があり、根拠や具体例を用いて聞き手を納得させられているか。
- **資料の品質:** 画面共有されている資料は、視覚的に分かりやすく、メッセージを効果的に補強しているか。
- **質疑応答:** 質問の意図を正確に理解し、的確に回答できているか。
""",
    "project_management": """
- **課題の明確化:** プロジェクトの現状、課題、リスクが明確に共有されているか。
- **原因分析と対策:** 課題の根本原因を深掘りし、具体的な解決策を議論できているか。
- **意思決定:** 複数の選択肢の中から、根拠を持って次のアクションを決定できているか。
- **タスク割り当て:** 「誰が」「何を」「いつまでに」行うかが明確に定義されているか。
- **推進力:** 会議全体を通して、プロジェクトを前進させるという強い意志が感じられるか。
""",
    "interview_candidate": """
- **論理的思考力:** 質問に対し、構造的で分かりやすい回答ができているか。
- **経験の具体性:** 自身の経験について、具体的な行動や結果を交えて語れているか。
- **自己分析:** 自身の強み・弱みを客観的に理解し、それを今後の成長にどう繋げるかを語れているか。
- **コミュニケーション能力:** 面接官の質問の意図を正しく汲み取り、円滑な対話ができているか。
- **志望動機と熱意:** 企業や職務への強い興味・関心を示し、入社後の貢献イメージを伝えられているか。
""",
    "interview_interviewer": """
- **質問の質:** 候補者の能力や経験を深掘りするための、的確な質問（行動評価質問など）ができているか。
- **傾聴姿勢:** 候補者の話を最後まで聞き、相槌や要約を通じて理解を示せているか。
- **候補者の魅力引き出し:** 候補者がリラックスして本来の力を発揮できるような雰囲気作りができているか。
- **魅力付け:** 会社のビジョンや仕事の魅力を、候補者の興味に合わせて伝えられているか。
- **評価の客観性:** 事実に基づいた評価を行い、バイアスのかかった質問や判断をしていないか。
"""
}

# 出力JSON形式の定義
VIDEO_ANALYSIS_JSON_OUTPUT_DEFINITION = """
```json
{
  "analysis_summary": {
    "detected_mode": "（例: presentation）",
    "overall_score": 85,
    "strengths": "（強みの解説文）",
    "areas_for_improvement": "（改善点の解説文）"
  },
  "speakers": [
    {
      "speaker_id": "話者1",
      "performance_scores": {
        "構成の論理性": 4,
        "話し方": 3,
        "説得力": 5,
        "資料の品質": 4,
        "質疑応答": 3
      },
      "feedback": [
        {
          "type": "Good Point",
          "timestamp": "00:02:15",
          "detail": "具体例を挙げて説明しており、非常に分かりやすいです。"
        },
        {
          "type": "To Improve",
          "timestamp": "00:05:30",
          "detail": "少し早口になっているため、もう少しゆっくり話すと聞きやすくなります。"
        }
      ]
    }
  ],
  "time_series_analysis": [
    {
      "time_segment": "00:00 - 01:00",
      "scores": {
        "総合パフォーマンス": 70,
        "話者の明瞭さ": 80,
        "参加者のエンゲージメント": 60
      }
    },
    {
      "time_segment": "01:00 - 02:00",
      "scores": {
        "総合パフォーマンス": 75,
        "話者の明瞭さ": 85,
        "参加者のエンゲージメント": 65
      }
    }
  ],
  "summarized_transcript": [
      { "speaker_id": "話者1", "text": "それでは、本日のアジェンダですが、まず..." },
      { "speaker_id": "話者2", "text": "その点について質問があります。" }
  ]
}
"""
