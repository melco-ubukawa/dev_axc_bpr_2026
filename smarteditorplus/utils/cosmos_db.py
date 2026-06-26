# utils/cosmos_db.py

import os
import logging
import threading
from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exceptions
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# --- ロガー設定 ---
logger = logging.getLogger(__name__)

# --- Cosmos DB 定数 ---
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")

# タスク用
COSMOS_DATABASE_TASKS_NAME = os.environ.get("COSMOS_DATABASE_TASKS_NAME", "TaskManagementDB")
COSMOS_CONTAINER_TASKS_NAME = os.environ.get("COSMOS_CONTAINER_TASKS_NAME", "AsyncTasks")
TASK_TTL_SECONDS = 3600

# セッション用 兼 データ分析用
COSMOS_DATABASE_SESSIONS_NAME = os.environ.get("COSMOS_DATABASE_SESSIONS_NAME", "ChatHistoryDB")
COSMOS_CONTAINER_SESSIONS_NAME = os.environ.get("COSMOS_CONTAINER_SESSIONS_NAME", "Sessions")

# Geminiログ用
COSMOS_DATABASE_NAME = os.environ.get("COSMOS_DATABASE_NAME")
COSMOS_CONTAINER_NAME = os.environ.get("COSMOS_CONTAINER_NAME")

# --- グローバル変数 (シングルトン) ---
tasks_cosmos_container = None
sessions_cosmos_container = None
gemini_log_cosmos_container = None
_in_memory_storage = {}

class InMemoryContainer:
    """
    Cosmos DBコンテナの動作を模倣する、スレッドセーフなインメモリ実装。
    本番環境での使用は非推奨。
    """
    def __init__(self, partition_key_path):
        self.partition_key = partition_key_path.strip('/')
        # 各コンテナタイプごとに専用のストレージとロックを用意
        storage_key = self.__class__.__name__ + partition_key_path
        if storage_key not in _in_memory_storage:
            _in_memory_storage[storage_key] = {}
        self.store = _in_memory_storage[storage_key]
        self.lock = threading.Lock()

    def create_item(self, body):
        with self.lock:
            item_id = body.get('id')
            if item_id in self.store:
                raise Exception(f"Item with id '{item_id}' already exists.")
            self.store[item_id] = body
            logger.info(f"Item {item_id} created in in-memory storage.")
            return body

    def read_item(self, item, partition_key):
        with self.lock:
            return self.store.get(item)

    def upsert_item(self, body):
        with self.lock:
            item_id = body.get('id')
            self.store[item_id] = body
            return body

    def query_items(self, query, enable_cross_partition_query=False):
        with self.lock:
            return list(self.store.values())

    def delete_item(self, item, partition_key):
        with self.lock:
            if item in self.store:
                # パーティションキーが一致するか簡易的にチェック
                stored_item_pk_value = self.store[item].get(self.partition_key.strip('/'))
                if stored_item_pk_value == partition_key:
                    del self.store[item]
                    logger.info(f"Item {item} deleted from in-memory storage.")
                else:
                    # 本来のCosmosDBの挙動に合わせて404エラーを発生させる
                    raise cosmos_exceptions.CosmosResourceNotFoundError(f"Item with id '{item}' not found in partition '{partition_key}'.")
            else:
                 raise cosmos_exceptions.CosmosResourceNotFoundError(f"Item with id '{item}' not found.")

# --- Azure Cosmos DB for Tasks クライアント初期化 (シングルトン) ---
def get_tasks_container():
    """タスク管理用コンテナをシングルトンとして取得・初期化する。"""
    global tasks_cosmos_container
    if tasks_cosmos_container is None:
        if not all([COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE_TASKS_NAME, COSMOS_CONTAINER_TASKS_NAME]):
            logger.warning("Cosmos DB for tasks connection info is not set. Task management is disabled.")
            return None
        try:
            client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
            database = client.create_database_if_not_exists(id=COSMOS_DATABASE_TASKS_NAME)
            tasks_cosmos_container = database.create_container_if_not_exists(
                id=COSMOS_CONTAINER_TASKS_NAME,
                partition_key=PartitionKey(path="/id"),
                default_ttl=TASK_TTL_SECONDS
            )
            logger.info(f"Cosmos DB container '{COSMOS_CONTAINER_TASKS_NAME}' for tasks is ready.")
        except Exception as e:
            logger.error(f"Failed to initialize Cosmos DB container for tasks: {e}", exc_info=True)
    return tasks_cosmos_container

# --- Cosmos DB タスク操作ヘルパー関数 ---
def create_task_in_cosmos(task_data: dict):
    container = get_tasks_container()
    if not container: return None
    try:
        task_data['id'] = task_data.get('task_id')
        container.create_item(body=task_data)
        return task_data
    except cosmos_exceptions.CosmosHttpResponseError as e:
        logger.error(f"Failed to create task in Cosmos DB: {e}", exc_info=True)
        return None

def get_task_from_cosmos(task_id: str):
    container = get_tasks_container()
    if not container: return None
    try:
        return container.read_item(item=task_id, partition_key=task_id)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Failed to read task {task_id} from Cosmos DB: {e}", exc_info=True)
        return None

def update_task_in_cosmos(task_id: str, updates: dict):
    """
    タスクを更新する。
    基本はPatch APIを使用してRUを節約するが、
    ドキュメントが存在しない場合やPatchに失敗した場合は、
    安全策として従来のRead-Modify-Write (Upsert) にフォールバックする。
    """
    container = get_tasks_container()
    if not container:
        return None
    
    # 1. まず Patch (部分更新) を試みる [推奨・低負荷]
    # Cosmos DB の Patch API は1回あたり最大10操作の制限がある
    MAX_PATCH_OPERATIONS = 10
    try:
        patch_operations = []
        for key, value in updates.items():
            patch_operations.append({"op": "add", "path": f"/{key}", "value": value})

        if len(patch_operations) > MAX_PATCH_OPERATIONS:
            # 制限超過時は直接 Read-Modify-Write にフォールバック
            raise Exception(f"Patch operations count ({len(patch_operations)}) exceeds limit ({MAX_PATCH_OPERATIONS}), using Read-Modify-Write.")

        updated_item = container.patch_item(
            item=task_id,
            partition_key=task_id,
            patch_operations=patch_operations
        )
        return updated_item

    except cosmos_exceptions.CosmosResourceNotFoundError:
        # 2. 対象が存在しない場合 (新規作成のケースなど)
        # Patchはできないため、Upsertを行う必要があるが、
        # updates は部分データかもしれないため、注意が必要。
        # ここでは「新規作成」として扱うか、ログを出して諦めるかの判断が必要だが、
        # 既存動作維持のため、updatesの内容でアイテムを作成/更新を試みる。
        
        # idが含まれていない場合は追加
        if "id" not in updates:
            updates["id"] = task_id
            
        try:
            # upsert_item で新規作成/上書き
            return container.upsert_item(body=updates)
        except Exception as e:
            logger.error(f"Failed to upsert task {task_id} (fallback): {e}", exc_info=True)
            return None

    except Exception as e:
        # 3. Patchで予期せぬエラーが出た場合 (例: スキーマ不整合など)
        # 念のため従来の方法 (Read -> Update -> Upsert) でリトライする
        logger.warning(f"Patch failed for task {task_id}, falling back to Read-Modify-Write. Error: {e}")
        
        try:
            # 既存データを読んで
            item = container.read_item(item=task_id, partition_key=task_id)
            # 更新して
            item.update(updates)
            # 書き込む
            return container.upsert_item(body=item)
        except Exception as retry_error:
            logger.error(f"Failed to update task {task_id} after retry: {retry_error}", exc_info=True)
            return None


# --- Azure Cosmos DB for Sessions & Analyzer クライアント初期化 (シングルトン) ---
def get_sessions_container():
    """会話履歴とデータ分析プロジェクト保存用コンテナをシングルトンとして取得・初期化する。"""
    global sessions_cosmos_container
    if sessions_cosmos_container is None:
        if not all([COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE_SESSIONS_NAME, COSMOS_CONTAINER_SESSIONS_NAME]):
            logger.warning("Cosmos DB for Sessions/Analyzer connection info is not set. Persistence is disabled.")
            sessions_cosmos_container = 'disabled'
            return None
        try:
            client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
            database = client.create_database_if_not_exists(id=COSMOS_DATABASE_SESSIONS_NAME)
            container = database.create_container_if_not_exists(
                id=COSMOS_CONTAINER_SESSIONS_NAME,
                partition_key=PartitionKey(path="/userId")
            )
            logger.info(f"Cosmos DB container '{COSMOS_CONTAINER_SESSIONS_NAME}' for sessions and analyzer is ready.")
            sessions_cosmos_container = container
        except Exception as e:
            logger.error(f"Failed to initialize Cosmos DB container for sessions/analyzer: {e}", exc_info=True)
            sessions_cosmos_container = 'disabled'
            return None
    if sessions_cosmos_container == 'disabled':
        return None
    return sessions_cosmos_container

# --- Azure Cosmos DB for Gemini Logs クライアント初期化 (シングルトン) ---
def get_cosmos_container():
    """Gemini API呼び出しログ用コンテナをシングルトンとして取得・初期化する。"""
    global gemini_log_cosmos_container
    if gemini_log_cosmos_container is None:
        if not all([COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE_NAME, COSMOS_CONTAINER_NAME]):
            logger.warning("Cosmos DB connection info for logs is not set. Logging is disabled.")
            gemini_log_cosmos_container = 'disabled'
            return None
        try:
            client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
            database = client.create_database_if_not_exists(id=COSMOS_DATABASE_NAME)
            container = database.create_container_if_not_exists(
                id=COSMOS_CONTAINER_NAME,
                partition_key=PartitionKey(path="/userId")
            )
            logger.info(f"Cosmos DB container '{COSMOS_CONTAINER_NAME}' for logs is ready.")
            gemini_log_cosmos_container = container
        except Exception as e:
            logger.error(f"Failed to initialize Cosmos DB container for logs: {e}", exc_info=True)
            gemini_log_cosmos_container = 'disabled'
            return None
    if gemini_log_cosmos_container == 'disabled':
        return None
    return gemini_log_cosmos_container

# === 設計アシスタント & データ分析プロジェクト用 ヘルパー関数 (共用) ===

def get_design_project_container():
    """
    設計プロジェクトおよびデータ分析プロジェクト保存用のコンテナを取得します。
    (セッション用コンテナを共用)
    """
    return get_sessions_container()

def create_design_project(project_data: dict):
    """
    新しい設計プロジェクトまたは分析プロジェクトをCosmos DBに作成します。
    """
    container = get_design_project_container()
    if not container:
        logger.error("Project container is not available. Cannot create project.")
        return None
    try:
        if 'id' not in project_data or 'userId' not in project_data:
            logger.error("Project data must contain 'id' and 'userId'.")
            return None
        
        container.create_item(body=project_data)
        logger.info(f"Successfully created new project with ID: {project_data['id']}")
        return project_data
    except cosmos_exceptions.CosmosHttpResponseError as e:
        if e.status_code == 409:
             logger.error(f"Failed to create project: An item with ID '{project_data.get('id')}' already exists.")
        else:
             logger.error(f"Failed to create project in Cosmos DB (HTTP {e.status_code}): {e.message}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while creating project: {e}", exc_info=True)
        return None

def get_design_project(project_id: str, user_id: str):
    """
    指定されたIDの設計プロジェクトまたは分析プロジェクトをCosmos DBから取得します。
    """
    container = get_design_project_container()
    if not container:
        logger.error("Project container is not available. Cannot get project.")
        return None
    try:
        logger.info(f"Reading project '{project_id}' for user '{user_id}'...")
        return container.read_item(item=project_id, partition_key=user_id)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        logger.warning(f"Project '{project_id}' not found for user '{user_id}'.")
        return None
    except Exception as e:
        logger.error(f"Failed to read project {project_id} from Cosmos DB: {e}", exc_info=True)
        return None

def update_design_project(project_id: str, user_id: str, updates: dict):
    """
    設計プロジェクトまたは分析プロジェクトのデータを更新します。
    """
    container = get_design_project_container()
    if not container:
        logger.error("Project container is not available. Cannot update project.")
        return None
    try:
        item = container.read_item(item=project_id, partition_key=user_id)
        item.update(updates)
        updated_item = container.upsert_item(body=item)
        logger.info(f"Successfully updated project '{project_id}'.")
        return updated_item
    except cosmos_exceptions.CosmosResourceNotFoundError:
        logger.warning(f"Project '{project_id}' not found for update in Cosmos DB.")
        return None
    except Exception as e:
        logger.error(f"Failed to update project {project_id} in Cosmos DB: {e}", exc_info=True)
        return None
    
def get_projects_by_user(user_id: str):
    """
    指定されたユーザーIDに紐づくすべての分析プロジェクトを、作成日時の降順で取得します。
    一覧表示に必要な一部のフィールド（ID、プロジェクト名、目的、ステータス、作成日時）のみを返します。
    """
    container = get_sessions_container()
    try:
        # InMemoryContainer はSQLクエリを解釈しないため、手動でフィルタリングとソートを行う
        if isinstance(container, InMemoryContainer):
            with container.lock:
                all_items = list(container.store.values())
            
            user_projects = [
                item for item in all_items 
                if item.get("userId") == user_id and item.get("type") == "AnalysisProject"
            ]
            # createdAtで降順ソート
            user_projects.sort(key=lambda x: x.get('createdAt', '1970-01-01T00:00:00.000Z'), reverse=True)
            
            # 必要なフィールドのみを抽出して返す
            return [
                {
                    "id": p.get("id"),
                    "projectName": p.get("projectName"),
                    "analysisPurpose": p.get("analysisPurpose"),
                    "status": p.get("status"),
                    "createdAt": p.get("createdAt")
                } for p in user_projects
            ]

        # Cosmos DB を使用する場合のクエリ
        query = "SELECT c.id, c.projectName, c.analysisPurpose, c.status, c.createdAt FROM c WHERE c.userId = @userId AND c.type = 'AnalysisProject' ORDER BY c.createdAt DESC"
        parameters = [{"name": "@userId", "value": user_id}]
        
        # パーティションキーがuserIdなので、パーティション内クエリとして効率的に実行
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            partition_key=user_id
        ))
        logger.info(f"Cosmos DBからユーザー '{user_id}' のために {len(items)} 件のプロジェクトを取得しました。")
        return items
        
    except Exception as e:
        logger.error(f"ユーザー {user_id} のプロジェクト一覧クエリでエラー: {e}", exc_info=True)
        return []

def delete_design_project(project_id: str, user_id: str):
    """
    指定されたIDのプロジェクトまたはモデルをCosmos DBまたはインメモリから削除します。
    （セッション用コンテナを共用）
    """
    container = get_design_project_container()
    try:
        # Cosmos DB SDKのdelete_itemメソッドを呼び出す
        container.delete_item(item=project_id, partition_key=user_id)
        logger.info(f"Successfully deleted item '{project_id}' for user '{user_id}' from {'Cosmos DB' if not isinstance(container, InMemoryContainer) else 'memory'}.")
        return True
    except cosmos_exceptions.CosmosResourceNotFoundError:
        # 削除対象が見つからない場合はエラーとせず、警告ログのみ記録して正常終了とする
        logger.warning(f"Item '{project_id}' for user '{user_id}' not found for deletion. It might have been already deleted.")
        return True
    except Exception as e:
        logger.error(f"Failed to delete item {project_id} from storage: {e}", exc_info=True)
        return False

def get_models_by_project_id(project_id: str, user_id: str):
    """
    指定されたプロジェクトIDに紐づくすべての予測モデルを取得します。
    """
    container = get_sessions_container()
    try:
        # InMemoryContainerの場合は手動でフィルタリング
        if isinstance(container, InMemoryContainer):
            with container.lock:
                all_items = list(container.store.values())
            
            project_models = [
                item for item in all_items 
                if item.get("userId") == user_id 
                and item.get("type") == "PredictionModel"
                and item.get("project_id") == project_id
            ]
            return project_models

        # Cosmos DB の場合はSQLクエリで検索
        query = "SELECT * FROM c WHERE c.userId = @userId AND c.type = 'PredictionModel' AND c.project_id = @projectId"
        parameters = [
            {"name": "@userId", "value": user_id},
            {"name": "@projectId", "value": project_id}
        ]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            partition_key=user_id  # パーティションキーを指定してクエリを効率化
        ))
        return items
        
    except Exception as e:
        logger.error(f"プロジェクト {project_id} に紐づくモデルのクエリでエラー: {e}", exc_info=True)
        return []
    
def update_gemini_log_usage(log_id: str, user_id: str, usage_data: dict):
    """
    指定されたログエントリに入力・出力トークン情報を追記更新する
    """
    container = get_cosmos_container()
    if not container or container == 'disabled':
        return

    try:
        # パーティションキーが必要なため、user_id (またはログ作成時に使用したキー) を引数で受け取る
        item = container.read_item(item=log_id, partition_key=user_id)
        
        # パラメータにトークン情報を追記
        if "usage" not in item:
            item["usage"] = {}
        
        item["usage"].update({
            "prompt_tokens": usage_data.get("prompt_tokens", 0),
            "completion_tokens": usage_data.get("completion_tokens", 0),
            "total_tokens": usage_data.get("total_tokens", 0)
        })
        
        container.upsert_item(body=item)
        logger.info(f"Updated usage stats for log {log_id}")
    except Exception as e:
        logger.error(f"Failed to update usage for log {log_id}: {e}", exc_info=True)

def get_design_projects_by_user(user_id: str):
    """
    【追加】指定されたユーザーIDに紐づく設計アシスタント用プロジェクトを取得します。
    SoftwareDesignProjectタイプを対象とし、updatedAtでソートします。
    """
    container = get_sessions_container()
    try:
        # InMemoryContainer対応
        if isinstance(container, InMemoryContainer):
            with container.lock:
                all_items = list(container.store.values())
            
            user_projects = [
                item for item in all_items 
                if item.get("userId") == user_id and item.get("type") == "SoftwareDesignProject"
            ]
            # 更新日時順にソート
            user_projects.sort(key=lambda x: x.get('updatedAt', ''), reverse=True)
            
            # top.htmlが必要とするフィールドを返す
            return [
                {
                    "id": p.get("id"),
                    "projectName": p.get("projectName"),
                    "developerLevel": p.get("developerLevel"),
                    "status": p.get("status"),
                    "updatedAt": p.get("updatedAt")
                } for p in user_projects
            ]

        # Cosmos DB クエリ
        # type = 'SoftwareDesignProject' でフィルタリング
        query = "SELECT c.id, c.projectName, c.developerLevel, c.status, c.updatedAt FROM c WHERE c.userId = @userId AND c.type = 'SoftwareDesignProject' ORDER BY c.updatedAt DESC"
        parameters = [{"name": "@userId", "value": user_id}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            partition_key=user_id
        ))
        logger.info(f"Cosmos DB: Fetched {len(items)} design projects for user '{user_id}'.")
        return items
        
    except Exception as e:
        logger.error(f"Error fetching design projects for user {user_id}: {e}", exc_info=True)
        return []