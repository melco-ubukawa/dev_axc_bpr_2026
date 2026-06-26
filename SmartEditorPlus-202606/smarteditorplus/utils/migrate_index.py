import os
import json
import time
from dotenv import load_dotenv

# Azure SDKのインポート
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
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
)

# 環境変数の読み込み
load_dotenv()

SERVICE_ENDPOINT = os.environ.get("AZURE_SEARCH_SERVICE_ENDPOINT")
ADMIN_KEY = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME")

# --- インデックス定義をここに直接記述（依存関係を排除するため） ---
def create_search_index_manual():
    print(f"--- インデックス '{INDEX_NAME}' の定義を作成中 ---")
    
    credential = AzureKeyCredential(ADMIN_KEY)
    index_client = SearchIndexClient(endpoint=SERVICE_ENDPOINT, credential=credential)
    
    # ★ここが改修ポイント：content_for_llm を searchable=True に設定
    target_fields = [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="persona_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="user_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="original_filename", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="chunk_id", type=SearchFieldDataType.String),
        SearchField(name="content_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="original_file_blob_name", type=SearchFieldDataType.String, retrievable=True),
        
        # 【重要】ここを searchable=True に変更済み
        SearchField(name="content_for_llm", type=SearchFieldDataType.String, searchable=True, retrievable=True),
        
        SearchField(name="summary_for_search", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="keywords_for_search", type=SearchFieldDataType.Collection(SearchFieldDataType.String), searchable=True, filterable=True),
        SearchField(
            name="content_vector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True, vector_search_dimensions=3072, vector_search_profile_name="my-hnsw-profile",
        ),
    ]

    # ベクトル検索・セマンティック検索の設定
    vector_search = VectorSearch(
        algorithms=[{"name": "my-hnsw-config", "kind": "hnsw"}],
        profiles=[VectorSearchProfile(name="my-hnsw-profile", algorithm_configuration_name="my-hnsw-config")],
    )
    semantic_config = SemanticConfiguration(
        name="my-semantic-config",
        prioritized_fields=SemanticPrioritizedFields(content_fields=[SemanticField(field_name="summary_for_search")])
    )
    semantic_search = SemanticSearch(configurations=[semantic_config])

    # インデックスオブジェクトの作成
    index = SearchIndex(
        name=INDEX_NAME,
        fields=target_fields,
        vector_search=vector_search,
        semantic_search=semantic_search
    )

    # インデックスの作成（または更新）
    index_client.create_or_update_index(index)
    print(f"-> インデックス '{INDEX_NAME}' を正常に作成しました。")

# --- メイン移行ロジック ---
def migrate_index():
    if not all([SERVICE_ENDPOINT, ADMIN_KEY, INDEX_NAME]):
        print("エラー: 環境変数が設定されていません。.envファイルを確認してください。")
        return

    credential = AzureKeyCredential(ADMIN_KEY)
    search_client = SearchClient(endpoint=SERVICE_ENDPOINT, index_name=INDEX_NAME, credential=credential)
    index_client = SearchIndexClient(endpoint=SERVICE_ENDPOINT, credential=credential)

    print(f"=== 移行処理開始: {INDEX_NAME} ===")

    # 1. 既存データのバックアップ
    print("\n1. 既存データをダウンロード中...")
    backup_data = []
    try:
        # 全件取得（上限1000万件）
        results = search_client.search(search_text="*", select="*", top=10000000)
        for doc in results:
            backup_data.append(doc)
        print(f"   -> {len(backup_data)} 件のドキュメントを取得しました。")
        
        if len(backup_data) == 0:
            print("   警告: データが0件です。処理を終了します。")
            return

        # バックアップ保存
        with open("backup_data.json", "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        print("   -> 'backup_data.json' にバックアップを保存しました。")

    except Exception as e:
        print(f"   エラー: データの取得に失敗しました。\n   {e}")
        return

    # 2. 既存インデックスの削除
    print("\n2. 旧インデックスを削除中...")
    try:
        index_client.delete_index(INDEX_NAME)
        print("   -> 削除完了。")
        # 削除が反映されるまで少し待機
        time.sleep(5)
    except Exception as e:
        print(f"   エラー: インデックスの削除に失敗しました。\n   {e}")
        return

    # 3. 新しい定義でインデックスを再作成
    print("\n3. 新しい設定でインデックスを再作成中...")
    try:
        create_search_index_manual()
        # 作成が反映されるまで少し待機
        time.sleep(5)
    except Exception as e:
        print(f"   エラー: インデックスの再作成に失敗しました。\n   {e}")
        return

    # 4. データの復元
    print("\n4. データを新しいインデックスに復元中...")
    try:
        batch_size = 500
        total_uploaded = 0
        for i in range(0, len(backup_data), batch_size):
            batch = backup_data[i : i + batch_size]
            search_client.upload_documents(documents=batch)
            total_uploaded += len(batch)
            print(f"   -> {total_uploaded} / {len(backup_data)} 件 完了")
            time.sleep(1) # 負荷軽減のため少し待機
        
        print("\n=== 移行処理が正常に完了しました ===")
        print("これまでのデータで全文検索が可能になっています。")

    except Exception as e:
        print(f"   エラー: データのアップロード中に問題が発生しました。\n   {e}")
        print("   ※データは 'backup_data.json' に残っています。")

if __name__ == "__main__":
    migrate_index()