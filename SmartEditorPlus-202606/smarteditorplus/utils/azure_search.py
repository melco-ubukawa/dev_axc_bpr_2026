import os
import re
import logging
from datetime import datetime, timezone, timedelta
from azure.core.exceptions import ResourceNotFoundError
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticPrioritizedFields,
    SemanticField,
    SemanticSearch,
    ScoringProfile,
    FreshnessScoringFunction,
    FreshnessScoringParameters,
)
from openai import AzureOpenAI
from . import gemini
from google.genai import types
import config
import time

# 鮮度スコアリングプロファイル名
FRESHNESS_SCORING_PROFILE_NAME = "freshness-profile"

logger = logging.getLogger(__name__)

# --- 環境変数から設定を読み込み ---
SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_SERVICE_ENDPOINT")
SEARCH_ADMIN_KEY = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME")

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")

# --- 埋め込みモデルのクライアントを初期化 ---
openai_client = None
if all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME]):
    openai_client = AzureOpenAI(
        api_version="2024-02-01", # 最新の安定バージョン
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
    )
else:
    logger.warning("Azure OpenAI embedding settings are not fully configured.")

def _extract_date_from_filename(filename: str) -> str | None:
    """【Step1】ファイル名から日付を正規表現で抽出する。"""
    # YYYY-MM-DD or YYYY/MM/DD (最も具体的なパターンから優先)
    m = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', filename)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYYMMDD (8桁: 有効な月・日のみ)
    m = re.search(r'(?<!\d)(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', filename)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYY-MM or YYYY/MM (年月のみ)
    m = re.search(r'(\d{4})[-/](0[1-9]|1[0-2])(?![-/\d])', filename)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYYMM (6桁: 有効な月のみ)
    m = re.search(r'(?<!\d)(\d{4})(0[1-9]|1[0-2])(?!\d)', filename)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    return None


def _extract_date_from_text(text_head: str) -> str | None:
    """【Step2】本文冒頭500文字から日付を正規表現で抽出する（和暦含む）。"""
    # 令和N年M月D日
    m = re.search(r'令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text_head)
    if m:
        try:
            dt = datetime(2018 + int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # 令和N年M月 (日なし)
    m = re.search(r'令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月', text_head)
    if m:
        try:
            dt = datetime(2018 + int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # 平成N年M月D日
    m = re.search(r'平成\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text_head)
    if m:
        try:
            dt = datetime(1988 + int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYY年M月D日
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text_head)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYY/MM/DD or YYYY-MM-DD
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text_head)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYYMMDD (8桁)
    m = re.search(r'(?<!\d)(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', text_head)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYY/MM or YYYY-MM (年月のみ)
    m = re.search(r'(\d{4})[-/](0[1-9]|1[0-2])(?![-/\d])', text_head)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    # YYYYMM (6桁: 有効な月のみ)
    m = re.search(r'(?<!\d)(\d{4})(0[1-9]|1[0-2])(?!\d)', text_head)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

    return None


def _extract_date_with_ai(text_head: str, filename: str, user_info: dict) -> str | None:
    """【Step3】生成AIを使ってテキスト冒頭から日付を抽出する（use_ai_extraction=True の場合のみ呼び出す）。"""
    prompt = (
        f"以下のドキュメントの冒頭テキストから、そのドキュメントが作成または更新された日付を抽出してください。\n"
        f"日付が見つかった場合は YYYY-MM-DD 形式のみで返答してください。\n"
        f"日付が見つからない場合や不明な場合は「不明」とだけ返答してください。\n\n"
        f"ファイル名: {filename}\n"
        f"冒頭テキスト:\n{text_head}\n\n"
        f"日付 (YYYY-MM-DD形式、または「不明」):"
    )
    try:
        result, _ = gemini.generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            system_instruction="あなたはドキュメントから日付を抽出する専門家です。指定された形式のみで回答してください。",
            max_output_tokens=32,
            temperature=0.0,
            user_info=user_info,
            skip_cosmos_log=True,
            feature_category="RAG Date Extraction",
        )
        if result and not result.startswith("[エラー:") and result.strip() != "不明":
            date_match = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', result.strip())
            if date_match:
                dt = datetime(
                    int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)),
                    tzinfo=timezone.utc
                )
                return dt.isoformat()
    except Exception as e:
        logger.warning(f"[日付抽出] Step3 AI抽出中にエラー (ファイル: {filename}): {e}")
    return None


def extract_date_from_document(
    filename: str,
    raw_text_head: str | None = None,
    use_ai_extraction: bool = False,
    user_info: dict | None = None,
) -> str:
    """
    ドキュメントの更新日(last_updated)を4段階のヒューリスティックで推定して返す。

    優先順位:
      Step1: ファイル名の正規表現チェック
      Step2: 本文冒頭500文字の正規表現チェック
      Step3: 生成AIによる抽出 (use_ai_extraction=True かつ user_info が存在する場合のみ)
      Step4: 最終手段 — 現在のUTC日時

    戻り値: ISO 8601形式の日付文字列 (例: "2024-05-20T00:00:00+00:00")
    """
    # --- Step1: ファイル名 ---
    date_str = _extract_date_from_filename(filename)
    if date_str:
        logger.info(f"[日付抽出] Step1 ファイル名から取得: {date_str} (ファイル: {filename})")
        return date_str

    # --- Step2: 本文冒頭 ---
    if raw_text_head:
        date_str = _extract_date_from_text(raw_text_head[:500])
        if date_str:
            logger.info(f"[日付抽出] Step2 本文冒頭から取得: {date_str} (ファイル: {filename})")
            return date_str

    # --- Step3: 生成AI ---
    if use_ai_extraction and user_info and raw_text_head:
        date_str = _extract_date_with_ai(raw_text_head[:500], filename, user_info)
        if date_str:
            logger.info(f"[日付抽出] Step3 AIにより取得: {date_str} (ファイル: {filename})")
            return date_str

    # --- Step4: 現在日時（最終手段） ---
    fallback = datetime.now(timezone.utc).isoformat()
    logger.info(f"[日付抽出] Step4 日付を特定できず現在日時を使用 (ファイル: {filename})")
    return fallback


def get_embedding(text: str) -> list[float] | None:
    """指定されたテキストをAzure OpenAIでベクトル化する"""
    if not openai_client:
        logger.error("Azure OpenAI client is not initialized.")
        return None
    try:
        # text-embedding-3-largeの出力次元数は3072
        embedding = openai_client.embeddings.create(input=[text], model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME)
        return embedding.data[0].embedding
    except Exception as e:
        logger.error(f"Error getting embedding from Azure OpenAI: {e}", exc_info=True)
        return None


def get_embeddings_batch(texts: list[str], batch_size: int = 100) -> list[list[float] | None]:
    """
    複数テキストをバッチでベクトル化する。
    Azure OpenAI Embeddings APIは input に複数テキストを受け付けるため、
    個別の get_embedding を繰り返すよりも大幅に高速。
    各テキストは独立にベクトル化されるため、出力は個別呼び出しと数学的に同一。
    """
    if not openai_client:
        logger.error("Azure OpenAI client is not initialized.")
        return [None] * len(texts)

    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            response = openai_client.embeddings.create(
                input=batch,
                model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME
            )
            for item in response.data:
                results[i + item.index] = item.embedding
        except Exception as e:
            logger.error(f"Error getting batch embeddings (batch {i // batch_size + 1}): {e}", exc_info=True)
            # 失敗したバッチの要素は None のまま

    succeeded = sum(1 for r in results if r is not None)
    logger.info(f"Batch embedding completed: {succeeded}/{len(texts)} texts succeeded.")
    return results

def create_search_index_if_not_exists():
    """
    【改訂版】Azure AI Searchにインデックスが存在しない場合は作成し、
    存在する場合はフィールド定義を比較して不足分を更新する。
    """
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME]):
        logger.error("Azure AI Search settings are not configured. Cannot create or update index.")
        return

    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=credential)
        
        # --- ターゲットとなる最新のフィールド定義 ---
        target_fields = [
            SearchField(name="id", type=SearchFieldDataType.String, key=True),
            SearchField(name="persona_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchField(name="user_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchField(name="original_filename", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="chunk_id", type=SearchFieldDataType.String),
            SearchField(name="content_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchField(name="original_file_blob_name", type=SearchFieldDataType.String, retrievable=True),
            SearchField(name="content_for_llm", type=SearchFieldDataType.String, searchable=True, retrievable=True),
            SearchField(name="summary_for_search", type=SearchFieldDataType.String, searchable=True),
            SearchField(name="keywords_for_search", type=SearchFieldDataType.Collection(SearchFieldDataType.String), searchable=True, filterable=True),
            SearchField(
                name="content_vector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True, vector_search_dimensions=3072, vector_search_profile_name="my-hnsw-profile",
            ),
            # 鮮度スコアリング用日付フィールド
            SearchField(name="last_updated", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            SearchField(name="registered_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        ]

        try:
            # 1. 既存のインデックス定義を取得
            existing_index = index_client.get_index(SEARCH_INDEX_NAME)
            existing_field_names = {field.name for field in existing_index.fields}
            
            new_fields_added = False
            # 2. ターゲットのフィールドが既存のインデックスに存在するかチェック
            for target_field in target_fields:
                if target_field.name not in existing_field_names:
                    logger.info(f"Field '{target_field.name}' not found in existing index '{SEARCH_INDEX_NAME}'. Adding it...")
                    existing_index.fields.append(target_field)
                    new_fields_added = True
            
            # 3. スコアリングプロファイルの確認・追加
            existing_profile_names = {p.name for p in (existing_index.scoring_profiles or [])}
            if FRESHNESS_SCORING_PROFILE_NAME not in existing_profile_names:
                freshness_profile = ScoringProfile(
                    name=FRESHNESS_SCORING_PROFILE_NAME,
                    functions=[
                        FreshnessScoringFunction(
                            field_name="last_updated",
                            boost=2.0,
                            parameters=FreshnessScoringParameters(boosting_duration=timedelta(days=365)),
                            interpolation="linear",
                        )
                    ],
                    function_aggregation="sum",
                )
                if existing_index.scoring_profiles is None:
                    existing_index.scoring_profiles = []
                existing_index.scoring_profiles.append(freshness_profile)
                new_fields_added = True  # スコアリングプロファイル追加もインデックス更新対象
                logger.info(f"Scoring profile '{FRESHNESS_SCORING_PROFILE_NAME}' will be added to index '{SEARCH_INDEX_NAME}'.")

            # 4. 新しいフィールドやプロファイルがあれば、インデックスを更新
            if new_fields_added:
                logger.info(f"Updating index '{SEARCH_INDEX_NAME}' with new fields/profiles...")
                index_client.create_or_update_index(existing_index)
                logger.info(f"Index '{SEARCH_INDEX_NAME}' updated successfully.")
            else:
                logger.info(f"Index '{SEARCH_INDEX_NAME}' is already up-to-date.")

        except ResourceNotFoundError:
            # --- インデックスが存在しない場合は新規作成 ---
            logger.info(f"Index '{SEARCH_INDEX_NAME}' not found. Creating a new one...")
            vector_search = VectorSearch(
                algorithms=[{"name": "my-hnsw-config", "kind": "hnsw"}],
                profiles=[VectorSearchProfile(name="my-hnsw-profile", algorithm_configuration_name="my-hnsw-config")],
            )
            semantic_config = SemanticConfiguration(
                name="my-semantic-config",
                prioritized_fields=SemanticPrioritizedFields(content_fields=[SemanticField(field_name="summary_for_search")])
            )
            semantic_search = SemanticSearch(configurations=[semantic_config])

            # 鮮度スコアリングプロファイル: last_updated が新しいほどスコアを底上げする
            freshness_profile = ScoringProfile(
                name=FRESHNESS_SCORING_PROFILE_NAME,
                functions=[
                    FreshnessScoringFunction(
                        field_name="last_updated",
                        boost=2.0,
                        parameters=FreshnessScoringParameters(boosting_duration=timedelta(days=365)),
                        interpolation="linear",
                    )
                ],
                function_aggregation="sum",
            )

            index = SearchIndex(
                name=SEARCH_INDEX_NAME,
                fields=target_fields,
                vector_search=vector_search,
                semantic_search=semantic_search,
                scoring_profiles=[freshness_profile],
            )

            index_client.create_index(index)
            logger.info(f"Successfully created search index '{SEARCH_INDEX_NAME}'.")

    except Exception as e:
        logger.error(f"Failed to create or update search index '{SEARCH_INDEX_NAME}': {e}", exc_info=True)

        
def upload_documents_to_search_index(documents: list[dict]):
    """ドキュメントのリストをAI Searchインデックスにアップロードする（バッチ処理・待機処理対応版）"""
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME]):
        logger.error("Azure AI Search settings are not configured. Cannot upload documents.")
        return False
        
    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=SEARCH_INDEX_NAME, credential=credential)
        
        # --- バッチサイズ設定 ---
        batch_size = 500 
        total_docs = len(documents)
        all_success = True
        
        logger.info(f"Starting upload of {total_docs} documents in batches of {batch_size}...")

        for i in range(0, total_docs, batch_size):
            batch = documents[i : i + batch_size]
            try:
                # アップロード実行
                result = search_client.upload_documents(documents=batch)
                
                # 結果確認
                failed_in_batch = [item for item in result if not item.succeeded]
                if failed_in_batch:
                    logger.error(f"Batch {i//batch_size + 1}: {len(failed_in_batch)} documents failed to upload.")
                    all_success = False
                else:
                    logger.info(f"Batch {i//batch_size + 1}: Successfully uploaded {len(batch)} documents.")
                
                # 【追加】バッチ間の待機：連続リクエストによる負荷を下げるため0.5秒待つ
                time.sleep(0.5)

            except Exception as batch_e:
                batch_e_str = str(batch_e)
                # インデックスが存在しない or フィールド定義が古い場合のエラーハンドリング
                _needs_index_update = (
                    "IndexNotFound" in batch_e_str
                    or isinstance(batch_e, ResourceNotFoundError)
                    or "does not exist on type 'search.documentFields'" in batch_e_str
                )
                if _needs_index_update:
                    if "IndexNotFound" in batch_e_str or isinstance(batch_e, ResourceNotFoundError):
                        logger.warning(f"Index '{SEARCH_INDEX_NAME}' not found. Attempting to create it now...")
                    else:
                        logger.warning(
                            f"Index '{SEARCH_INDEX_NAME}' is missing required fields. "
                            f"Attempting to update index schema now..."
                        )

                    # インデックス作成 or フィールド追加
                    create_search_index_if_not_exists()

                    # インデックス更新が反映されるまで待機
                    logger.info("Waiting for index schema update to become active...")
                    time.sleep(3)

                    # 再試行（このバッチのみ）
                    try:
                        logger.info(f"Retrying batch {i//batch_size + 1} upload...")
                        search_client.upload_documents(documents=batch)
                        logger.info(f"Batch {i//batch_size + 1}: Retry successful.")
                    except Exception as retry_e:
                        logger.error(f"Batch {i//batch_size + 1} retry failed: {retry_e}")
                        all_success = False
                else:
                    logger.error(f"Batch {i//batch_size + 1} failed with unexpected error: {batch_e}", exc_info=True)
                    all_success = False
        
        return all_success

    except Exception as e:
        logger.error(f"Failed to upload documents to search index (Fatal): {e}", exc_info=True)
        return False

def _transform_query_with_llm(query_text: str, mode: str, user_info: dict) -> list[str]:
    """LLMを使って検索クエリを変換する内部ヘルパー関数。"""
    if not user_info:
        logger.warning("クエリ変換にはユーザー情報が必要ですが、提供されませんでした。変換をスキップします。")
        return [query_text]

    if mode == 'hyde':
        prompt = f"""以下の質問に対する、仮の回答ドキュメントを生成してください。このドキュメントはベクトル検索の精度向上のために使われます。事実として正しい必要はありませんが、質問に答えるであろう理想的なドキュメントを想像して記述してください。

質問: {query_text}

仮の回答ドキュメント:"""
        system_instruction = "あなたは、質問に対して検索用の仮回答ドキュメントを生成するAIです。応答はドキュメントのテキストのみとしてください。"
        
        parent_blob_name = gemini.ensure_parent_log_blob_name(
            user_info=user_info,
            feature_category="RAG Query Transformation",
            model_name=config.DEFAULT_MODEL_NAME,
            additional_log_params={"mode": "hyde"},
        )

        hypothetical_answer, _ = gemini.generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            system_instruction=system_instruction,
            max_output_tokens=1024,
            temperature=0.4,
            user_info=user_info,
            skip_cosmos_log=True,
            feature_category="RAG Query Transformation (HyDE)",
            parent_log_id=parent_blob_name,
        )
        if hypothetical_answer and not hypothetical_answer.startswith("[エラー:"):
            logger.info("HyDEによる仮回答の生成に成功しました。")
            return [hypothetical_answer]
        logger.warning("HyDEによる仮回答の生成に失敗しました。元のクエリを使用します。")
        return [query_text]

    elif mode == 'multi_query':
        prompt = f"""あなたは、ユーザーの質問を、検索に最適化された複数の異なる視点からの検索クエリに書き換えるAIアシスタントです。
元の質問の意図を保持しつつ、同義語、関連用語、異なる表現を用いて、3つのバリエーション豊かな検索クエリを生成してください。
出力は、各クエリを改行で区切ったテキストのみとしてください。番号や説明は不要です。

元の質問: {query_text}

生成された3つの検索クエリ:"""
        system_instruction = "あなたは、検索クエリを生成する専門家です。応答は生成されたクエリのみとし、番号や説明は含めないでください。"
        
        parent_blob_name = gemini.ensure_parent_log_blob_name(
            user_info=user_info,
            feature_category="RAG Query Transformation",
            model_name=config.DEFAULT_MODEL_NAME,
            additional_log_params={"mode": "multi_query"},
        )

        multi_queries_str, _ = gemini.generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            system_instruction=system_instruction,
            max_output_tokens=512,
            temperature=0.6,
            user_info=user_info,
            skip_cosmos_log=True,
            feature_category="RAG Query Transformation (Multi-Query)",
            parent_log_id=parent_blob_name,
        )
        if multi_queries_str and not multi_queries_str.startswith("[エラー:"):
            queries = [q.strip() for q in multi_queries_str.split('\n') if q.strip()]
            if queries:
                logger.info(f"Multi-queryによるクエリ拡張に成功。生成されたクエリ数: {len(queries)}")
                return queries
        logger.warning("Multi-queryによるクエリ拡張に失敗しました。元のクエリを使用します。")
        return [query_text]
        
    return [query_text]

def search_knowledge_base(query_text: str, persona_id: str, top_k: int = 5, transform_query_mode: str | None = None, user_info: dict | None = None, use_freshness_boost: bool = False) -> list[dict]:
    """
    指定されたクエリに基づき、AI Searchから関連性の高いチャンクを検索する。
    ハイブリッド検索（ベクトル + キーワード + セマンティック）と、動的なクエリ変換（HyDE, multi_query）を実行する。
    【改訂版】ハイブリッド・インデックス戦略に対応。
    """
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME]):
        logger.error("Azure AI Search settings are not configured. Cannot perform search.")
        return []

    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT, 
            index_name=SEARCH_INDEX_NAME, 
            credential=credential
        )
        
        search_query_for_keywords = query_text
        search_query_for_vector = query_text

        if transform_query_mode and user_info:
            if transform_query_mode == 'hyde':
                logger.info(f"RAG HyDEモードでクエリ変換を実行: '{query_text}'")
                hypothetical_answer = _transform_query_with_llm(query_text, 'hyde', user_info)
                search_query_for_vector = hypothetical_answer
            elif transform_query_mode == 'multi_query':
                logger.info(f"RAG Multi-queryモードでクエリ変換を実行: '{query_text}'")
                generated_queries = _transform_query_with_llm(query_text, 'multi_query', user_info)
                search_query_for_keywords = " | ".join(generated_queries)
        
        query_vector = get_embedding(search_query_for_vector)
        if not query_vector:
            logger.error("クエリテキストのベクトル化に失敗しました。検索を中止します。")
            return []
        
        vector_query = VectorizedQuery(
            vector=query_vector, 
            k_nearest_neighbors=top_k * 5, # セマンティックリランキングのためにより多くの候補を取得
            fields="content_vector"
        )

        scoring_profile_name = FRESHNESS_SCORING_PROFILE_NAME if use_freshness_boost else None
        logger.info(
            f"Azure AI Search をハイブリッド検索(v2)で実行。"
            f"キーワード: '{search_query_for_keywords[:100]}...', "
            f"鮮度ブースト: {use_freshness_boost}"
        )
        results = search_client.search(
            search_text=search_query_for_keywords,
            vector_queries=[vector_query],
            search_fields=["content_for_llm", "summary_for_search", "keywords_for_search"],
            filter=f"persona_id eq '{persona_id}'",
            query_type="semantic",
            semantic_configuration_name='my-semantic-config',
            scoring_profile=scoring_profile_name,
            top=top_k,
            select=["original_filename", "content_for_llm", "last_updated"]
        )

        search_results = []
        for result in results:
            item = {
                "filename": result.get("original_filename", "不明なファイル"),
                "content": result.get("content_for_llm", ""),
                "score": result.get("@search.score", 0.0),
                "reranker_score": result.get("@search.reranker_score", 0.0),
                "last_updated": result.get("last_updated"),
            }
            search_results.append(item)
            logger.debug(
                f"  [chunk] file={item['filename']}, "
                f"score={item['score']:.4f}, "
                f"reranker={item['reranker_score']:.4f}, "
                f"last_updated={item['last_updated']}"
            )

        search_results.sort(key=lambda x: x["reranker_score"], reverse=True)

        logger.info(f"{len(search_results)}件の関連チャンクをAzure AI Searchから取得しました (ペルソナ: '{persona_id}')。")

        # 鮮度ブースト有効時: 上位5件のスコアと更新日をINFOログに出力
        if use_freshness_boost and search_results:
            logger.info("[鮮度スコアリング結果] 上位チャンクのスコアと更新日:")
            for rank, r in enumerate(search_results[:5], start=1):
                logger.info(
                    f"  #{rank}: score={r['score']:.4f}, reranker={r['reranker_score']:.4f}, "
                    f"last_updated={r['last_updated']}, file={r['filename']}"
                )

        return search_results

    except Exception as e:
        logger.error(f"ナレッジベースの検索中にエラーが発生: {e}", exc_info=True)
        return []
    
def delete_documents_by_persona_id(persona_id: str) -> bool:
    """指定されたpersona_idに一致するすべてのドキュメントをAzure AI Searchから削除する。"""
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME]):
        logger.error("Azure AI Search settings not configured. Cannot delete documents.")
        return False
    if not persona_id:
        logger.warning("persona_id not provided. Skipping deletion.")
        return False

    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=SEARCH_INDEX_NAME, credential=credential)
        
        # 1. persona_idでフィルタリングして、削除対象のドキュメントのIDを取得
        results = search_client.search(search_text="*", filter=f"persona_id eq '{persona_id}'", select=["id"])
        
        documents_to_delete = [{"id": doc["id"]} for doc in results]
        
        if not documents_to_delete:
            logger.info(f"No documents found for persona_id '{persona_id}'. Nothing to delete.")
            return True

        # 2. 取得したIDリストを使ってドキュメントを削除
        logger.info(f"Attempting to delete {len(documents_to_delete)} documents for persona_id '{persona_id}'...")
        result = search_client.delete_documents(documents=documents_to_delete)
        
        successful_deletes = [item for item in result if item.succeeded]
        if len(successful_deletes) == len(documents_to_delete):
            logger.info(f"Successfully deleted {len(documents_to_delete)} documents for persona_id '{persona_id}'.")
            return True
        else:
            logger.error(f"Failed to delete some documents for persona_id '{persona_id}'.")
            return False

    except ResourceNotFoundError:
        logger.warning(f"Index '{SEARCH_INDEX_NAME}' not found during deletion. Assuming already clean.")
        return True # インデックス自体がなければ削除成功とみなす
    except Exception as e:
        logger.error(f"Error deleting documents by persona_id '{persona_id}': {e}", exc_info=True)
        return False
    
def delete_documents_by_filename(persona_id: str, filename: str) -> bool:
    """指定されたpersona_idとファイル名に一致するドキュメントを削除する。"""
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME, persona_id, filename]):
        logger.error("Azure AI Search settings, persona_id, or filename are missing.")
        return False

    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=SEARCH_INDEX_NAME, credential=credential)
        
        # 1. persona_idとfilenameの両方でフィルタリングしてIDを取得
        filter_query = f"persona_id eq '{persona_id}' and original_filename eq '{filename}'"
        results = search_client.search(search_text="*", filter=filter_query, select=["id"])
        
        documents_to_delete = [{"id": doc["id"]} for doc in results]
        
        if not documents_to_delete:
            logger.info(f"No documents found for persona_id '{persona_id}' and filename '{filename}'.")
            return True

        # 2. 取得したIDリストを使ってドキュメントを削除
        logger.info(f"Attempting to delete {len(documents_to_delete)} documents for filename '{filename}'...")
        result = search_client.delete_documents(documents=documents_to_delete)
        
        successful_deletes = [item for item in result if item.succeeded]
        if len(successful_deletes) == len(documents_to_delete):
            logger.info(f"Successfully deleted documents for filename '{filename}'.")
            return True
        else:
            logger.error(f"Failed to delete some documents for filename '{filename}'.")
            return False

    except Exception as e:
        logger.error(f"Error deleting documents by filename '{filename}': {e}", exc_info=True)
        return False
    
def get_all_chunks_for_file(persona_id: str, filename: str) -> str | None:
    """指定されたpersona_idとファイル名に一致するすべてのチャンクを取得し、結合して返す。"""
    if not all([SEARCH_ENDPOINT, SEARCH_ADMIN_KEY, SEARCH_INDEX_NAME, persona_id, filename]):
        logger.error("Azure AI Search settings, persona_id, or filename are missing for chunk retrieval.")
        return None
    
    try:
        credential = AzureKeyCredential(SEARCH_ADMIN_KEY)
        search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=SEARCH_INDEX_NAME, credential=credential)
        
        safe_filename = filename.replace("'", "''")
        filter_query = f"persona_id eq '{persona_id}' and original_filename eq '{safe_filename}'"
        
        # チャンクをすべて取得するために、topパラメータを大きな値に設定
        results = search_client.search(
            search_text="*", 
            filter=filter_query, 
            select=["content_for_llm", "chunk_id"],
            top=10000 # 1ファイルあたりのチャンク数の上限として十分な値を設定
        )
        
        chunks = []
        for doc in results:
            try:
                # chunk_idを数値に変換してソートに備える
                chunk_id_num = int(doc["chunk_id"])
                chunks.append({
                    "id": chunk_id_num,
                    "content": doc["content_for_llm"]
                })
            except (ValueError, TypeError):
                logger.warning(f"Invalid chunk_id '{doc['chunk_id']}' found for file '{filename}'. Skipping this chunk.")
                continue

        if not chunks:
            logger.warning(f"No chunks found for persona_id '{persona_id}' and filename '{filename}'.")
            return None
        
        # chunk_idで昇順にソートして、元のファイルの順序を復元
        sorted_chunks = sorted(chunks, key=lambda x: x['id'])
        full_content = "".join(chunk['content'] for chunk in sorted_chunks)
        
        logger.info(f"Retrieved and combined {len(chunks)} chunks for file '{filename}'. Total length: {len(full_content)} chars.")
        return full_content

    except Exception as e:
        logger.error(f"Error getting all chunks for file '{filename}': {e}", exc_info=True)
        return None