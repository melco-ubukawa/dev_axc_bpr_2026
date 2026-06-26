import json
import logging
import re
from google.genai import types
from .gemini import generate_with_gemini
from .diagram_gen import DiagramGenerator
import config
import time

logger = logging.getLogger(__name__)

# ==========================================
# ドキュメント定義と依存関係の定義
# dependencies: このドキュメントを作成するために参照すべき他のドキュメントID
# ==========================================
DOCUMENT_DEFINITIONS = {
    # === 000 プロジェクト計画 ===
    "000_010": {
        "title": "プロジェクト概要",
        "dependencies": [],
        "instruction": """
        プロジェクトの全体像を明確に定義してください。以下の項目を必ず含めてください：
        1. **背景**: なぜこのプロジェクトが必要なのか、現状の課題や市場環境。
        2. **目的**: プロジェクトを通じて達成したい究極的なゴール（KGI）。
        3. **必要性**: 今やらなければならない理由、ビジネス上のインパクト。
        4. **期待される効果**: 定量的・定性的なメリット（例：コスト〇〇%削減、ユーザー満足度向上）。
        5. **前提条件・制約事項**: 予算、期間、技術的制約、法的要件など。
        """,
        "diagram_type": "markmap", 
        "diagram_instruction": "プロジェクトの核となる「背景」「目的」「ゴール」「主要なステークホルダー」を中心ノードとし、そこから詳細な要素を展開するマインドマップを作成してください。"
    },
    "000_020": {
        "title": "プロジェクトスコープ",
        "dependencies": ["000_010"],
        "instruction": """
        「プロジェクト概要」に基づき、プロジェクトの範囲を厳密に定義してください。曖昧さを排除するため、以下の形式で記述してください：
        - **対象業務範囲**: どの業務プロセスが含まれるか。
        - **対象システム範囲**: 開発する機能、連携する外部システム。
        - **対象ユーザー・期間**: 利用者層、サービス提供時間帯。
        - **【重要】対象外（Out of Scope）**: 明確に実施しないこと、次フェーズに回すことをリストアップしてください。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "「スコープ定義」を中心とし、「対象範囲（In Scope）」と「対象外（Out of Scope）」を左右に分け、機能や業務ごとに詳細をぶら下げたツリー図を作成してください。"
    },
    "000_030": {
        "title": "課題と解決アプローチ",
        "dependencies": ["000_010"],
        "instruction": """
        現状の課題（As-Is）からあるべき姿（To-Be）へのギャップを埋める方針を策定してください。
        表形式またはリスト形式で、以下の対応関係を明確にしてください：
        - **課題**: 具体的に何が問題か。
        - **真因**: なぜその問題が起きているか。
        - **解決策の候補**: 複数の案（案A, 案B...）。
        - **採用アプローチ**: 選定した案とその選定理由（コスト、実現性、効果の観点から）。
        """,
        "diagram_type": "graphviz",
        "diagram_instruction": "「課題」から「原因」を経て「解決策」に至るロジックツリー、または因果関係図（Cause-and-Effect Diagram）を作成してください。ノードの色を変えて（例：課題=赤、解決策=青）視認性を高めてください。"
    },
    "000_040": { # 元ID: 010_080 (上流工程へ移動)
        "title": "用語集",
        "dependencies": ["000_010"],
        "instruction": """
        プロジェクト内で使用する用語の定義を統一してください。
        **必ずMarkdownの表形式（用語、読み、英語名、定義）で記述してください。**
        特に、業界特有の用語、システム独自の概念、略語について、誤解が生じないように明確な定義、読み方、英語表記（変数名に使用するため）を記述してください。
        """,
        "diagram_type": None,
        "diagram_instruction": None
    },

    # === 010 要件定義（業務仕様） ===
    "010_010": {
        "title": "概念データフロー図",
        "dependencies": ["000_020"],
        "instruction": """
        スコープに含まれる業務における「情報の流れ」を定義してください。
        システム内部の詳細ではなく、**業務ユーザー視点**で、「誰が（アクター）」「いつ」「どんな情報を」「どのプロセスに入力し」「何が出力されるか」を記述してください。
        主要な業務シナリオ（例：注文受付〜出荷）ごとにフローを記述してください。
        """,
        "diagram_type": "graphviz",
        "diagram_instruction": "DFD（データフロー図）のレベル0またはレベル1相当の図を作成してください。外部エンティティ（ユーザー、他システム）、プロセス（処理）、データストア（ファイル、台帳）、データフロー（矢印）を明確に区別して描画してください。"
    },
    "010_020": {
        "title": "概念クラス図",
        "dependencies": ["010_010"],
        "instruction": """
        業務（DFD）に登場する主要な「概念（エンティティ）」と、それらの関係性を定義してください。
        システムの実装クラスではなく、**ビジネス上の用語**として定義してください（例：「顧客」「注文」「商品」）。
        各エンティティについて、主要な属性（管理すべき情報）を箇条書きで列挙してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "主要なエンティティ間の関係（1対多、多対多など）を示す概念クラス図を作成してください。属性は代表的なもののみ表示し、関係性（関連名、多重度）の記述を重視してください。"
    },
    "010_030": {
        "title": "ビジネスルール",
        "dependencies": ["010_010", "010_020"],
        "instruction": """
        業務プロセス（DFD）やデータ（クラス図）に対する判断基準や制約を明文化してください。
        - **判断ロジック**: 「もし〜ならば〜する」という条件分岐。
        - **計算式**: 金額計算、ポイント付与などの具体的な計算方法。
        - **制約**: 入力値の制限、必須項目、データの有効期限など。
        - **権限**: 誰がどの操作を許可されるか。
        各ルールには一意なID（BR-001等）を付与してください。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "ビジネスルールの分類と階層構造をマインドマップで表現してください。"
    },
    "010_040": {
        "title": "状態遷移図",
        "dependencies": ["010_020", "010_030"],
        "instruction": """
        業務上の主要なオブジェクト（例：注文、申請書、会員ステータス）が、ライフサイクルの中でどのように状態を変えるかを定義してください。
        - **状態一覧**: 取りうるすべてのステータス。
        - **遷移イベント**: 状態が変わるきっかけ（ユーザー操作、時間経過、システム処理）。
        - **遷移条件**: 遷移が許可される条件（ガード条件）。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "主要オブジェクト（最も複雑なステータスを持つもの）のステートマシン図を作成してください。初期状態から終了状態までの遷移、トリガーとなるイベント、遷移時のアクションを記述してください。"
    },
    "010_050": { # 元ID: 010_090 (クラス図の直後へ移動)
        "title": "業務データ辞書",
        "dependencies": ["010_020"],
        "instruction": """
        概念クラス図で定義されたエンティティの属性を詳細化し、各機能や画面で共通して使用されるデータ項目（ドメインデータ）の定義書を作成してください。
        **必ずMarkdownの表形式で記述してください。**

        | 項目名(論理名) | 物理名(英語) | 型/桁数 | 制約/許容値 | 説明 |
        |---|---|---|---|---|
        | ... | ... | ... | ... | ... |
        """,
        "diagram_type": None,
        "diagram_instruction": None
    },
    "010_060": { # 元ID: 010_050
        "title": "画面仕様(要件・プロトタイプ)",
        "dependencies": ["010_010", "000_020"],
        "instruction": """
        業務要件を満たすユーザーインターフェースを定義するとともに、**初期段階から実際にブラウザで操作・検証可能な動的プロトタイプ**を作成してください。
        
        1. **仕様定義（テキスト）**:
        - **画面一覧**: システムに必要な全画面の定義。
        - **UI要件**: 各画面の表示項目、レイアウト方針、操作要素。

        2. **プロトタイプ実装要件（HTML/JS）**:
        - **全画面の実装**: 定義したすべての画面（一覧、登録、詳細、設定など）を一つのHTML内に実装すること。
        - **SPA挙動による画面遷移**: リンクやメニュー押下時に、JavaScriptでDOM要素の表示/非表示（hiddenクラス等）を切り替え、ページリロードなしでスムーズに画面遷移すること。
        - **フルインタラクション**: すべてのボタン・入力フォームが操作可能であること。保存ボタン等の押下時には、トースト通知やモーダル等の視覚的フィードバックを実装すること。
        - **動的データ描画**: HTMLへのベタ書きではなく、JavaScript変数として定義したモックデータ（JSON配列）をループ処理でテーブルやリストに描画すること。これによりデータの追加・削除の挙動も疑似的に再現すること。

        出力コードは、Tailwind CSS（CDN）を使用し、外部ファイル不要で即動作する単一HTMLファイルにしてください。
        """,
        "diagram_type": "html",
        "diagram_instruction": "要件定義の内容を具現化した、**全画面遷移・全操作が可能な高忠実度プロトタイプ（High-Fidelity Prototype）**を作成してください。JavaScriptを用いて、メニューによる画面切り替え、フォームのバリデーション、モックデータの追加/削除による表示更新など、実際のアプリと同様のUXを体験できるコードを記述してください。"
    },
    "010_070": { # 元ID: 010_060
        "title": "業務イベント一覧",
        "dependencies": ["010_010"],
        "instruction": """
        業務プロセスを駆動する「イベント」を網羅的にリストアップしてください。
        **必ず以下のカラムを持つMarkdown表形式で記述してください。**

        | イベントID | イベント名 | 発生タイミング/トリガー | 入力情報 | 関連プロセス |
        |---|---|---|---|---|
        | EV-001 | ... | ... | ... | ... |

        - **イベント名**: 「〇〇が発生した」「〇〇を受け付けた」
        - **発生タイミング/トリガー**: いつ、何によって引き起こされるか。
        - **入力情報**: そのイベントで発生するデータ。
        - **関連する業務プロセス**: どの処理が起動されるか。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "イベントを発生源（ユーザー、タイマー、外部システム）ごとにグルーピングし、トリガーされるプロセスを紐付けたツリー図を作成してください。"
    },
    "010_080": { # 元ID: 010_070
        "title": "システム化機能一覧",
        "dependencies": ["000_020", "010_060", "010_070"],
        "instruction": """
        定義された「画面（UI機能）」と「イベント（バックエンド/バッチ機能）」を統合し、システムが実装すべき機能を網羅的にリストアップしてください。
        **必ず以下のカラムを持つMarkdown表形式で記述してください。** リスト形式や見出しでの列挙は禁止です。

        | 機能ID | 大分類(サブシステム) | 中分類(業務) | 機能名 | 機能概要 | 優先度 | 種別 |
        |---|---|---|---|---|---|---|
        | FUNC-001 | ... | ... | ... | ... | 高 | 画面 |

        - **機能ID**: 一意な識別子。
        - **機能概要**: 何をする機能か具体的に。
        - **優先度**: 高・中・低。
        - **種別**: 画面、バッチ、APIなど。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "システム全体の機能構成を俯瞰できる「機能階層図（ファンクションツリー）」を作成してください。"
    },
    "010_090": { # 元ID: 050_010 (アーキテクチャ設計の前へ移動)
        "title": "非機能要件",
        "dependencies": ["000_010"],
        "instruction": """
        機能面以外の品質要求（NFR）を具体的に数値目標を含めて定義してください。
        これらはインフラ構成やアーキテクチャ選定の入力となります。
        - **可用性**: 稼働率（例: 99.9%）、RTO（目標復旧時間）、RPO（目標復旧時点）。
        - **性能**: スループット（TPS）、レスポンスタイム（例: 95%ileで200ms以内）。
        - **拡張性**: ユーザー数やデータ量の増加に対するスケーリング方針。
        - **セキュリティ**: 認証、暗号化、監査ログ、脆弱性対策。
        - **運用・保守性**: ログ出力、監視、バックアップ、デプロイ方式。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "IPAの非機能要求グレードなどを参考に、非機能要件の項目と目標値を体系的に整理したマインドマップを作成してください。"
    },
    "010_100": {
        "title": "業務受け入れ条件",
        "dependencies": ["010_080", "010_030"],
        "instruction": """
        業務部門（ユーザー）がシステムの受け入れ判定を行うための基準（UAT基準）を定義してください。
        **可能な限り表形式、または構造化されたリストで記述してください。**
        「正常に登録できること」といった曖昧な記述ではなく、「〇〇画面で××を入力して保存ボタンを押すと、△△データベースにレコードが作成され、完了メッセージが表示されること」のように、**具体的な検証シナリオ**として記述してください。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "受け入れテストのシナリオを、業務プロセスごとに階層化したツリー図を作成してください。"
    },

    # === 020 アーキテクチャ設計 ===
    "020_010": {
        "title": "C4コンテキスト図",
        "dependencies": ["000_020"],
        "instruction": """
        システム全体を一つのブラックボックスとして捉え、周囲の環境との関係を定義してください。
        - **本システム**: 中央に配置。
        - **ユーザー**: システムを利用する人（役割別）。
        - **外部システム**: 連携する他システム（メールサーバー、決済GW、既存基幹システム等）。
        それぞれの間でやり取りされる情報の概要を記述してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "C4モデルの「System Context Diagram」を作成してください。`!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Context.puml` を使用し、Person, System, System_Extマクロを使って描画してください。"
    },
    "020_020": {
        "title": "C4コンテナ図",
        "dependencies": ["020_010", "010_080"],
        "instruction": """
        機能一覧に基づき、システム内部を「コンテナ（個別にデプロイ・実行される単位）」レベルで分解し、構成を定義してください。
        - **コンテナ例**: Webアプリケーション、SPA(Single Page Application)、モバイルアプリ、APIサーバー、データベース、ファイルストレージ。
        - **技術スタック**: 各コンテナで採用する言語、フレームワーク、DB製品名。
        - **通信**: コンテナ間の通信プロトコル（HTTP/JSON, gRPC, SQL等）。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "C4モデルの「Container Diagram」を作成してください。`!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml` を使用し、Container, ContainerDbマクロを使って描画してください。"
    },
    "020_030": {
        "title": "C4コンポーネント図",
        "dependencies": ["020_020"],
        "instruction": """
        主要なコンテナ（特にAPIサーバーなど）の内部構造を「コンポーネント」レベルで分解してください。
        - **コンポーネント**: Controller, Service, Repository, Facadeなどの論理的な部品。
        - **責務**: 各コンポーネントが担う役割。
        - **依存関係**: コンポーネント間の呼び出し関係。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "C4モデルの「Component Diagram」を作成してください。`!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Component.puml` を使用し、Componentマクロを使って主要機能の実装構成を描画してください。"
    },
    "020_040": {
        "title": "インフラ構成概要",
        "dependencies": ["020_020", "010_090"],
        "instruction": """
        コンテナの配置と非機能要件（可用性・セキュリティ）に基づき、システムの稼働環境となるインフラストラクチャの構成を定義してください。
        - **環境**: クラウド（AWS/Azure/GCP）かオンプレミスか。
        - **ネットワーク**: VPC, サブネット, ファイアウォール, ロードバランサの配置。
        - **コンピュート**: 仮想サーバー(EC2/VM), コンテナ(ECS/AKS), サーバーレス(Lambda/Functions)。
        - **ストレージ/DB**: R53, S3, RDS, DynamoDBなどのマネージドサービス構成。
        """,
        "diagram_type": "graphviz",
        "diagram_instruction": "ネットワークトポロジー図を作成してください。サブネットによるゾーニング（Public/Private）や、リクエストの経路、冗長化構成が分かるようにノードとエッジを配置してください。"
    },

    # === 030 詳細設計（実装） ===
    "030_010": { # 元ID: 030_030 (実装の最初へ移動)
        "title": "DB設計(論理/物理)",
        "dependencies": ["010_020", "010_050", "010_090"],
        "instruction": """
        概念クラス図とデータ辞書を元に、実装レベルのデータベーススキーマ設計を行ってください。
        非機能要件（性能）を考慮し、インデックスやパーティショニングも検討してください。
        - **ER図**: エンティティ間のリレーション。
        - **テーブル定義**: テーブル名（論理/物理）、カラム名、データ型、PK、Not Null、デフォルト値。
        - **インデックス**: パフォーマンス要件に基づくインデックス設計。
        正規化のレベルや、あえて非正規化する箇所があればその理由も記述してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "`ie` 表記法（Crow's foot notation）を使用したER図（Entity Relationship Diagram）を作成してください。エンティティには主要な属性（PK/FK含む）を記述してください。"
    },
    "030_020": { # 元ID: 040_010 (DBの後、シーケンスの前へ移動)
        "title": "API仕様",
        "dependencies": ["010_080", "030_010"],
        "instruction": """
        機能一覧で抽出されたAPI機能について、インターフェース仕様を定義してください。
        DB設計に基づき、リクエスト/レスポンスのデータ構造を具体化してください。
        - **エンドポイント**: URLパス、HTTPメソッド。
        - **リクエスト**: ヘッダー、パスパラメータ、クエリパラメータ、ボディ（JSONスキーマ）。
        - **レスポンス**: ステータスコードごとのボディ構造（正常系、エラー系）。
        - **認証・認可**: 必要なトークンやスコープ。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "APIのエンドポイント一覧と、主要なAPIのリクエスト/レスポンス構造を視覚化した図、またはAPI呼び出しのシーケンス概要図を作成してください。"
    },
    "030_030": { # 元ID: 030_010
        "title": "実装データフロー図",
        "dependencies": ["010_010", "020_020", "030_010"],
        "instruction": """
        概念DFDをシステム内部の視点で詳細化してください。
        業務レベルのDFDをブレイクダウンし、具体的な「テーブル」「API」「ファイル」レベルでの入出力を定義してください。
        バッチ処理や非同期処理を含む複雑なデータ連携がある場合は特に詳細に記述してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "システム内部のデータフロー図を作成してください。プロセス（楕円）、データストア（二本線または円筒）、外部実体（四角）を用い、具体的なデータ項目名をラベルに記載してください。"
    },
    "030_040": { # 元ID: 030_020
        "title": "実装クラス図",
        "dependencies": ["010_020", "020_030", "030_010"],
        "instruction": """
        コンポーネント図で定義されたモジュール内のクラス設計を行ってください。
        DB設計（ORMマッピング）との整合性を意識し、アプリケーションの核となるドメインモデルや主要な機能の実装クラスを設計してください。
        - **クラス**: クラス名、主要な属性、メソッド。
        - **関係**: 継承、インターフェースの実装、依存、集約、コンポジション。
        デザインパターン（Factory, Strategy, Observer等）を適用する場合はその旨も記述してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "詳細なUMLクラス図を作成してください。クラス間の関係性（多重度、関連名）を正確に記述し、主要な属性とメソッドを含めてください。"
    },
    "030_050": { # 元ID: 030_040
        "title": "シーケンス図",
        "dependencies": ["010_080", "020_030", "030_020"],
        "instruction": """
        システムの主要なユースケース（機能一覧に基づく）について、API呼び出しからDBアクセスまでのオブジェクト間の相互作用を時系列で詳細に記述してください。
        - **アクター**: ユーザー。
        - **オブジェクト**: 画面(UI)、コントローラ、サービス、リポジトリ、DB、外部システム。
        - **メッセージ**: メソッド呼び出し、戻り値。
        例外処理（エラー時のフロー）についても記述してください。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "詳細なUMLシーケンス図を作成してください。`alt/else` で正常系と異常系を分岐させ、`loop` で繰り返し処理を表現し、`activate/deactivate` でライフラインの活性区間を明示してください。"
    },
    "030_060": { # 元ID: 030_050
        "title": "実装画面仕様(高機能モック)",
        "dependencies": ["010_060", "030_020"],
        "instruction": """
        開発者が最終実装のゴールを完全にイメージできるよう、**実際にブラウザで動作・操作可能な高忠実度プロトタイプ（High-Fidelity Prototype）**の仕様とコードを作成してください。
        静的なHTMLだけでなく、JavaScriptを用いて以下の「動く仕様」を実装してください。

        1. **シングルページアプリケーション(SPA)挙動**:
        - ナビゲーションメニューやリンクをクリックすると、ページリロードなしでメインコンテンツエリアが切り替わること。
        - アプリケーションに含まれる全画面（ダッシュボード、一覧、詳細、フォーム、設定等）の要素を記述し、JavaScriptで表示状態（hiddenクラスの着脱等）を制御して画面遷移を実現すること。

        2. **完全なインタラクション**:
        - **全ボタン**: クリック時に適切なアクション（画面遷移、モーダル表示、完了トースト通知、ステータス変更）が視覚的に実行されること。
        - **フォーム**: 入力値のバリデーション（必須、形式チェック）が動作し、エラー表示や送信ボタンの活性/非活性が切り替わること。
        - **コンポーネント**: タブ切り替え、ドロップダウンメニュー、モーダルダイアログの開閉がスムーズに動作すること。

        3. **疑似データ管理**:
        - JavaScriptオブジェクトとしてモックデータ（JSON）を保持し、それをループ処理でHTMLにレンダリングすること。
        - 「登録」や「削除」操作を行った際、メモリ上のデータを更新し、画面（一覧テーブル等）を再描画して変更が反映されたように見せること。

        出力内容は、外部CSS/JSファイルを必要とせず、単一のHTMLファイルとして保存するだけで即座に動作するコードにしてください（Tailwind CSS等はCDN利用）。
        """,
        "diagram_type": "html",
        "diagram_instruction": "主要な全画面フローを含む、JavaScriptで動作する**完全なインタラクティブ・モックアップ**を作成してください。Tailwind CSSでモダンにデザインし、`<script>`タグ内に画面遷移ロジック、イベントハンドラ、モックデータ操作処理を記述して、ユーザーが実際にクリックして全機能を試せるレベルに仕上げてください。"
    },
    "030_070": { # 元ID: 030_060
        "title": "バッチ・ジョブ設計",
        "dependencies": ["010_080", "030_010"],
        "instruction": """
        機能一覧のバッチ処理について詳細設計を行ってください。
        入出力となるテーブル（DB設計）を明記し、バックグラウンドで実行されるバッチ処理の詳細を設計してください。
        **ジョブの一覧は必ずMarkdown表形式で記述してください。**

        | ジョブID | ジョブ名称 | 実行頻度 | トリガー | 処理概要 |
        |---|---|---|---|---|
        | JOB-001 | ... | 毎日0時 | タイマー | ... |

        詳細仕様については、各ジョブごとに以下の項目を記述してください：
        - **処理ロジック**: 入力データ、加工ルール、出力データ。
        - **排他制御**: 多重起動の防止、DBロックの方針。
        - **エラーハンドリング**: 失敗時のリトライ、アラート通知、リカバリ手順。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "バッチ処理のフローチャート（アクティビティ図）、またはジョブ間の依存関係を示すジョブネット図を作成してください。"
    },
    "030_080": { # 元ID: 040_020
        "title": "外部システムIF",
        "dependencies": ["020_010", "010_050"],
        "instruction": """
        外部システムとのデータ連携仕様を詳細に定義してください。
        - **接続方式**: SFTP, API, DBリンク, メッセージキュー等。
        - **データフォーマット**: CSV, Fixed Length, XML, JSON等のレイアウト定義（データ辞書と整合）。
        - **プロトコル**: 通信手順、暗号化方式。
        - **エラー処理**: 通信エラー時やデータ不正時の再送・検知ルール。
        """,
        "diagram_type": "plantuml",
        "diagram_instruction": "外部システムとの連携シーケンス図を作成してください。ハンドシェイク、データ転送、Ack/Nack応答の流れを明確にしてください。"
    },
    "030_090": { # 元ID: 040_030
        "title": "メッセージ仕様",
        "dependencies": ["020_020"],
        "instruction": """
        非同期メッセージング（Kafka, RabbitMQ, SQS等）を使用する場合のメッセージ定義です。
        - **トピック/キュー名**: 宛先の定義。
        - **メッセージ構造**: ヘッダー、ボディ（ペイロード）のJSONスキーマ。
        - **QoS**: At most once, At least once, Exactly once等の保証レベル。
        """,
        "diagram_type": None,
        "diagram_instruction": None
    },
    "030_100": { # 元ID: 050_040
        "title": "エラー処理",
        "dependencies": ["030_020", "010_090"],
        "instruction": """
        システム全体のエラーハンドリング方針を定義してください。
        - **エラーコード体系**: アプリケーション独自のエラーコードとHTTPステータスコードのマッピング。
        - **ログ出力基準**: エラーレベル（Error, Warn）ごとの通知・記録方針。
        - **ユーザーへの応答**: エラー発生時の画面表示やAPIレスポンスメッセージの標準化。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "エラーの種類と、それに対応する処理フロー（ログ、通知、リトライ、画面表示）を整理したツリー図を作成してください。"
    },

    # === 040 テスト・移行 ===
    "040_010": { # 元ID: 060_010
        "title": "テスト戦略",
        "dependencies": ["010_080", "010_100", "010_090"],
        "instruction": """
        品質を担保するためのテスト全体の方針を策定してください。
        - **テストレベル**: 単体、結合、システム、受入テストのそれぞれの目的と範囲。
        - **テスト環境**: ステージング、本番相当環境の構成。
        - **テストデータ**: 本番データのマスキング利用や合成データの生成方針。
        - **自動化方針**: CI/CDパイプラインでの自動テスト（Unit, E2E）の導入範囲。
        """,
        "diagram_type": "markmap",
        "diagram_instruction": "テストピラミッド（単体・結合・E2Eの比率）や、各テストフェーズの実施順序・依存関係を示す図を作成してください。"
    },
    "040_020": { # 元ID: 080_010
        "title": "業務移行方針",
        "dependencies": ["000_020", "030_010"],
        "instruction": """
        現行業務から新システムへの移行計画を策定してください。
        - **移行方式**: 一斉移行か、拠点別・機能別の段階的移行か。
        - **並行稼働**: 旧システムと新システムの並行稼働期間と、その間のデータ同期方法。
        - **教育・訓練**: ユーザー向けのマニュアル作成や説明会の実施計画。
        """,
        "diagram_type": "graphviz",
        "diagram_instruction": "移行のフェーズ（準備、リハーサル、本番移行、並行稼働、完全切替）と、各フェーズのマイルストーンを示すロードマップ図を作成してください。"
    },
    "090_ADR": {
        "title": "ADR 決定記録",
        "dependencies": [],
        "instruction": """
        アーキテクチャ上の重要な決定事項（Architecture Decision Records）を記録してください。
        各決定について以下を記述してください：
        - **タイトル**: 決定の内容。
        - **ステータス**: 提案中、承認済、却下、廃止。
        - **コンテキスト**: なぜその決定が必要だったか、背景と課題。
        - **決定**: 採用した選択肢。
        - **結果**: その決定によるメリットとデメリット（トレードオフ）。
        """,
        "diagram_type": None,
        "diagram_instruction": None
    }
}

class DesignAIHandler:
    def __init__(self, user_info):
        self.user_info = user_info
        self.diagram_gen = DiagramGenerator(user_info)

    def generate_initial_response(self, project_name, developer_level, initial_idea):
        """プロジェクト開始時のキックオフメッセージのみ生成"""
        role_def = self._get_role_definition(developer_level)
        prompt = f"""
{role_def}
新しいソフトウェア開発プロジェクト「{project_name}」を開始します。
ユーザーからの初期アイデア概要: {initial_idea}

**あなたのタスク:**
1.  **initial_message**: ユーザーへの挨拶と、要件をより具体化するために不可欠な質問を3つ、箇条書きで提示してください。

**厳格な出力形式 (JSONのみ):**
{{
  "initial_message": "..."
}}
"""
        return self._call_gemini(prompt, "初期生成(挨拶)", response_mime_type="application/json")

    def generate_infographic(self, project_name, concept_text):
        """コンセプトを表すインフォグラフィック画像を生成する"""
        prompt = f"""
        System Command: Create a high-quality infographic image.
        
        **Topic:** Software Concept for "{project_name}"
        **Key Information to Visualize:**
        {concept_text[:500]}...
        
        **Design Style:**
        - Modern, clean, tech-oriented business infographic.
        - Use icons, flowcharts, and structured layouts.
        - Color scheme: Professional blue, white, and grey tones.
        - Aspect Ratio: 16:9.
        
        **IMPORTANT LANGUAGE CONSTRAINT:**
        - Any text appearing inside the image MUST be in **JAPANESE (日本語)**.
        - Translate key concepts into Japanese for labels and titles within the image.
        """
        
        try:
            response_iterator = generate_with_gemini(
                model_name=config.IMAGE_GENERATION_MODEL_NAME,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                stream=True,
                user_info=self.user_info,
                feature_category="コンセプト画像生成",
                aspect_ratio="16:9"
            )
            
            for chunk in response_iterator:
                if hasattr(chunk, 'image_data_url') and chunk.image_data_url:
                    return chunk.image_data_url
            
            return None
        except Exception as e:
            logger.error(f"Infographic generation failed: {e}")
            return None

    def generate_document_draft(self, doc_id, project_name, initial_idea, developer_level, all_documents=None):
        """
        指定されたドキュメントの初期ドラフトを生成する。
        dependencies に基づいて、他のドキュメントの内容をコンテキストとして取り込む。
        """
        if all_documents is None:
            all_documents = {}

        role_def = self._get_role_definition(developer_level)
        doc_def = DOCUMENT_DEFINITIONS.get(doc_id, {})
        
        # --- 依存ドキュメントのコンテキスト構築 ---
        context_str = ""
        dependencies = doc_def.get("dependencies", [])
        
        if dependencies:
            context_str += "\n\n## 参照すべき前提ドキュメント（以下の内容と整合性を取ってください）\n"
            found_dep = False
            for dep_id in dependencies:
                dep_content = all_documents.get(dep_id)
                # 既に生成済みで、中身がある場合のみコンテキストに追加
                if dep_content and len(dep_content.strip()) > 10:
                    dep_def = DOCUMENT_DEFINITIONS.get(dep_id, {})
                    title = dep_def.get("title", dep_id)
                    # コンテキストサイズ節約のため、最大文字数を制限（例: 2000文字）
                    truncated_content = dep_content[:3000] + "..." if len(dep_content) > 3000 else dep_content
                    context_str += f"\n### 【前提: {title}】\n{truncated_content}\n"
                    found_dep = True
            
            if not found_dep:
                context_str += "(前提となるドキュメントはまだ作成されていません。一般的なベストプラクティスに基づいて作成してください。)\n"
        
        # --- プロンプト構築 ---
        prompt = f"""
{role_def}
プロジェクト: {project_name}
概要: {initial_idea}

{context_str}

**作成対象:** {doc_id} ({doc_def.get('title')})
**内容指示:** {doc_def.get('instruction')}
**図解指示:** {doc_def.get('diagram_instruction')} (形式: {doc_def.get('diagram_type')})

**タスク:**
上記のプロジェクト概要、および【前提ドキュメント】の内容と矛盾しないように、
{doc_def.get('title')}のドラフト（Markdown）と図解コードを生成してください。
特に、機能名やデータ項目名などの用語は、前提ドキュメントで定義されているものを統一して使用してください。

**【最重要】厳格な出力形式 (JSONのみ):**
応答は以下のJSONオブジェクトの文字列のみとしてください。
**JSONの値（文字列）の中で改行を使用する場合は、必ず `\\n` とエスケープしてください。**
**ダブルクォーテーション `"` を使用する場合は `\\"` とエスケープしてください。**
**JSON以外のテキストを含めないでください。**

{{
  "content": "(Markdown本文。改行は \\n で表現)",
  "diagram_code": {{
      "type": "{doc_def.get('diagram_type') if doc_def.get('diagram_type') else 'null'}",
      "code": "(図解コード。改行は \\n で表現)" 
  }}
}}
"""
        result = self._call_gemini(prompt, f"ドラフト生成({doc_id})", response_mime_type="application/json")

        if doc_id == "000_010" and isinstance(result, dict) and result.get("content"):
            logger.info("Generating infographic for concept sheet...")
            infographic_data = self.generate_infographic(project_name, result["content"])
            if infographic_data:
                result["infographic"] = infographic_data

        return result

    def chat_and_update(self, current_doc_id, current_doc_content, chat_history, user_message, developer_level):
        role_def = self._get_role_definition(developer_level)
        
        doc_def = DOCUMENT_DEFINITIONS.get(current_doc_id, {})
        doc_title = doc_def.get("title", "不明なドキュメント")
        doc_instruction = doc_def.get("instruction", "内容を適切に更新してください。")
        diagram_type = doc_def.get("diagram_type")
        diagram_instruction = doc_def.get("diagram_instruction")

        diagram_prompt_part = ""
        if diagram_type:
            diagram_prompt_part = f"""
3.  **diagram_code**: このドキュメント(`{doc_title}`)の内容を補完する図解コードを生成してください。
    - **推奨形式:** `{diagram_type}`
    - **図解の詳細指示:** {diagram_instruction}
    - 変更がない場合や不要な場合は `null` を返してください。
"""

        recent_history = chat_history[-5:] if chat_history else []
        history_text = json.dumps(recent_history, ensure_ascii=False)

        prompt = f"""
{role_def}

**現在の編集対象ドキュメント:** {current_doc_id} ({doc_title})
**ドキュメント作成の詳細ガイドライン (この基準を満たすように更新してください):**
{doc_instruction}

**現在のドキュメント内容:**
```markdown
{current_doc_content}
```

**これまでの会話履歴:**
{history_text}

**ユーザーの最新発言:**
{user_message}

**あなたのタスク:**
1.  **assistant_message**: ユーザーへの回答を作成してください。
2.  **updated_document**: ユーザーの発言や議論の内容を反映して、ドキュメント全体を更新してください。**ガイドラインに従い、専門的かつ詳細な内容に書き換えてください。**
{diagram_prompt_part}

**【最重要】厳格な出力形式 (JSONのみ):**
応答は以下のJSONオブジェクトの文字列のみとしてください。
**JSONの値（文字列）の中で改行を使用する場合は、必ず `\\n` とエスケープしてください。**
**ダブルクォーテーション `"` を使用する場合は `\\"` とエスケープしてください。**

{{
  "assistant_message": "(ユーザーへの応答メッセージ)",
  "updated_document": "(更新後のドキュメント全文Markdown)",
  "diagram_code": {{
      "type": "{diagram_type if diagram_type else 'null'}",
      "code": "..." 
  }} OR null
}}
"""
        return self._call_gemini(prompt, f"対話更新 ({current_doc_id})", response_mime_type="application/json")

    def _call_gemini(self, prompt, category, response_mime_type="text/plain"):
        import time # インポート追加
        max_retries = 3
        current_prompt = prompt
        
        for attempt in range(max_retries):
            try:
                response_str, _ = generate_with_gemini(
                    model_name=config.SOFTWARE_DESIGN_MODEL_NAME,
                    contents=[types.Content(role="user", parts=[types.Part.from_text(text=current_prompt)])],
                    system_instruction="あなたはJSON形式で構造化されたデータを出力するAIアシスタントです。必ずvalidなJSONのみを返してください。",
                    max_output_tokens=65535,
                    temperature=0.4,
                    user_info=self.user_info,
                    feature_category=f"設計アシスタント({category}) - Try {attempt+1}",
                    skip_cosmos_log=False,
                    generation_config_override={"response_mime_type": response_mime_type}
                )

                if not response_str or response_str.startswith("[エラー:"):
                    logger.error(f"Gemini API returned error: {response_str}")
                    time.sleep(2)
                    continue

                if response_mime_type == "application/json":
                    cleaned_str = response_str.strip()
                    if cleaned_str.startswith("```json"):
                        cleaned_str = cleaned_str[7:]
                    elif cleaned_str.startswith("```"):
                        cleaned_str = cleaned_str[3:]
                    if cleaned_str.endswith("```"):
                        cleaned_str = cleaned_str[:-3]
                    
                    cleaned_str = cleaned_str.strip()
                    cleaned_str = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]', '', cleaned_str)

                    try:
                        parsed_json = json.loads(cleaned_str, strict=False)
                        if "content" in parsed_json or "assistant_message" in parsed_json:
                            return parsed_json
                        else:
                            raise ValueError("JSON parsed but missing required keys")

                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning(f"JSON Parse Failed (Attempt {attempt+1}/{max_retries}): {e}")
                        
                        if attempt < max_retries - 1:
                            current_prompt = f"""
{prompt}

---
**SYSTEM WARNING:**
前回の出力はJSON形式として不正でした。
エラー内容: {str(e)}
直前の出力(抜粋): {cleaned_str[:500]}...

**指示:**
必ず文法的に正しいJSON形式で、エスケープ漏れがないように再生成してください。
JSON以外のテキストは一切含めないでください。
"""
                            time.sleep(1)
                            continue
                        else:
                            return {
                                "content": f"【生成エラー】AI応答の解析に失敗しました。\n\nRaw output:\n```\n{cleaned_str}\n```", 
                                "diagram_code": None,
                                "assistant_message": "申し訳ありません。応答の生成中にエラーが発生しました。"
                            }

                return response_str

            except Exception as e:
                logger.error(f"Error in design_ai: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return {}

    def _get_role_definition(self, level):
        base = "あなたはソフトウェア開発のプロフェッショナルです。"
        if level == "Level 1":
            return f"{base} ビジネスオーナー相手に平易な言葉で対話し、技術詳細は補完してください。"
        elif level == "Level 3":
            return f"{base} ITアーキテクト相手に厳密な仕様と図解（UML/C4等）を提案してください。"
        return base
