import os
import json
import uuid
import logging
from datetime import datetime, timezone
from azure.cosmos import PartitionKey, exceptions as cosmos_exceptions
from .cosmos_db import get_sessions_container, COSMOS_DATABASE_SESSIONS_NAME, COSMOS_ENDPOINT, COSMOS_KEY
from .storage import get_blob_service_client

logger = logging.getLogger(__name__)

SHAREPOINT_TOOL_CONTAINER_NAME = "SharePointTools"
SHAREPOINT_TOOL_BLOB_CONTAINER = "sharepoint-tools"
TOKEN_LIMIT_PER_DAY = 10_000_000  # 後方互換用（非推奨）
TOKEN_LIMIT_STANDARD = 10_000_000  # 通常モデル: 1000万tokens/日/ツール
TOKEN_LIMIT_ADVANCED = 3_000_000   # 高度モデル: 300万tokens/日/ツール

# 分類リスト（API等で参照用）
TOOL_CATEGORIES = [
    "データ管理", "タスク・進捗", "メール・通知", "スケジュール", 
    "ドキュメント管理", "分析・レポート", "検索・探索", "申請・承認", 
    "ユーティリティ", "未分類"
]

def get_tool_cosmos_container():
    from azure.cosmos import CosmosClient
    try:
        client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE_SESSIONS_NAME)
        container = database.create_container_if_not_exists(
            id=SHAREPOINT_TOOL_CONTAINER_NAME,
            partition_key=PartitionKey(path="/userId")
        )
        return container
    except Exception as e:
        logger.error(f"Failed to initialize SharePoint Tool Cosmos container: {e}")
        return None

def get_tool_blob_container_client():
    client = get_blob_service_client()
    if not client: return None
    container_client = client.get_container_client(SHAREPOINT_TOOL_BLOB_CONTAINER)
    try:
        if not container_client.exists():
            container_client.create_container()
    except Exception as e:
        logger.warning(f"Blob container check/creation failed: {e}")
    return container_client

def save_sharepoint_tool(
    user_id: str,
    tool_name: str,
    ui_html: str,
    logic_js: str,
    description: str = "",
    history: list = None,
    tool_id: str = None,
    folder_name: str = "未分類",
    is_public: bool = False,
    category: str = "未分類",
    author_name: str = None,
    python_code: str = "",
    connection_settings: dict = None
) -> str | None:
    is_new = tool_id is None
    if is_new:
        tool_id = str(uuid.uuid4())

    if category not in TOOL_CATEGORIES:
        logger.warning(f"Invalid category '{category}' received. Resetting to '未分類'.")
        category = "未分類"

    # Cosmos コンテナを先に取得し、更新時は真の所有者をサーバーサイドで特定する。
    # クライアント入力を一切信頼しないことで書き込み昇格を防止する。
    cosmos_container = get_tool_cosmos_container()
    if cosmos_container is None:
        logger.error("Failed to get Cosmos container for save_sharepoint_tool")
        return None

    existing_item = None
    if is_new:
        target_user_id = user_id
    else:
        # まず現在ユーザーのパーティションを直接確認（オーナー本人の更新は追加クエリ不要）
        try:
            existing_item = cosmos_container.read_item(item=tool_id, partition_key=user_id)
            target_user_id = user_id
        except cosmos_exceptions.CosmosResourceNotFoundError:
            # 現在ユーザーのパーティションにない場合、クロスパーティションで真の所有者を特定
            items = list(cosmos_container.query_items(
                query="SELECT c.userId FROM c WHERE c.id = @id AND c.type = 'SharePointTool' ORDER BY c.updatedAt DESC",
                parameters=[{"name": "@id", "value": tool_id}],
                enable_cross_partition_query=True
            ))
            if items:
                target_user_id = items[0]['userId']
                try:
                    existing_item = cosmos_container.read_item(item=tool_id, partition_key=target_user_id)
                except cosmos_exceptions.CosmosResourceNotFoundError:
                    pass
            else:
                # CosmosDBに存在しない場合は現在ユーザーで新規作成扱い
                target_user_id = user_id

    blob_name = f"{target_user_id}/{tool_id}.json"

    # Blobコンテンツ
    tool_content = {
        "ui": ui_html,
        "logic": logic_js,
        "python": python_code,
        "description": description,
        "history": history or []
    }

    try:
        blob_container = get_tool_blob_container_client()
        blob_client = blob_container.get_blob_client(blob_name)
        blob_client.upload_blob(json.dumps(tool_content, ensure_ascii=False), overwrite=True)

        current_time = datetime.now(timezone.utc).isoformat()

        # --- メタデータ値の決定 ---
        created_at = existing_item.get("createdAt", current_time) if existing_item else current_time
        like_count = existing_item.get("likeCount", 0) if existing_item else 0
        view_count = existing_item.get("viewCount", 0) if existing_item else 0
        last_accessed_at = existing_item.get("lastAccessedAt", current_time) if existing_item else current_time

        # author_name: 引数 > 既存 > デフォルト の優先順位
        final_author_name = author_name
        if not final_author_name:
            final_author_name = existing_item.get("authorName", "Unknown") if existing_item else "Unknown"

        final_connection_settings = connection_settings
        if final_connection_settings is None:
            final_connection_settings = existing_item.get("connectionSettings", {}) if existing_item else {}

        metadata = {
            "id": tool_id,
            "userId": target_user_id,
            "toolName": tool_name,
            "description": description,
            "folderName": folder_name,
            "blobPath": blob_name,
            "updatedAt": current_time,
            "createdAt": created_at,
            "lastAccessedAt": last_accessed_at,
            "type": "SharePointTool",
            "isPublic": is_public,
            "category": category,
            "authorName": final_author_name,
            "likeCount": like_count,
            "viewCount": view_count,
            "connectionSettings": final_connection_settings
        }
        
        cosmos_container.upsert_item(body=metadata)
        return tool_id
    except Exception as e:
        logger.error(f"Failed to save SharePoint tool: {e}", exc_info=True)
        return None

def increment_tool_view_count(tool_id: str, author_id: str) -> int:
    """ツールの閲覧数を+1し、最終アクセス日時を現在時刻に更新する"""
    try:
        container = get_tool_cosmos_container()
        current_time = datetime.now(timezone.utc).isoformat() # ★現在時刻を取得

        # patch_item で閲覧数と最終アクセス日を同時に更新
        updated_item = container.patch_item(
            item=tool_id,
            partition_key=author_id,
            patch_operations=[
                {"op": "incr", "path": "/viewCount", "value": 1},
                {"op": "set", "path": "/lastAccessedAt", "value": current_time}
            ],
            filter_predicate="FROM c WHERE c.type = 'SharePointTool'"
        )
        return updated_item.get("viewCount", 0)
    except Exception as e:
        logger.warning(f"Failed to increment view count for {tool_id}: {e}")
        return 0

def rename_sharepoint_tool(tool_id: str, user_id: str, new_name: str) -> bool:
    """ツールの名称のみを更新する"""
    try:
        cosmos_container = get_tool_cosmos_container()
        item = cosmos_container.read_item(item=tool_id, partition_key=user_id)
        item["toolName"] = new_name
        item["updatedAt"] = datetime.now(timezone.utc).isoformat()
        cosmos_container.upsert_item(body=item)
        return True
    except Exception as e:
        logger.error(f"Rename failed: {e}")
        return False

def get_sharepoint_tool(tool_id: str, author_id: str = None) -> dict | None:
    """
    ツール情報を取得する。
    author_idが指定されている場合はそのユーザーのPartitionから直接取得（高速）。
    指定がない場合はCross Partition Queryで探す（既存互換）。
    """
    try:
        cosmos_container = get_tool_cosmos_container()
        metadata = None

        if author_id:
            try:
                metadata = cosmos_container.read_item(item=tool_id, partition_key=author_id)
            except cosmos_exceptions.CosmosResourceNotFoundError:
                return None
        else:
            # 従来通りの検索（PartitionKey不明時）- updatedAt降順で最新を優先
            query = "SELECT * FROM c WHERE c.id = @toolId ORDER BY c.updatedAt DESC"
            items = list(cosmos_container.query_items(query=query, parameters=[{"name": "@toolId", "value": tool_id}], enable_cross_partition_query=True))
            if not items: return None
            metadata = items[0]

        blob_container = get_tool_blob_container_client()
        blob_data = blob_container.get_blob_client(metadata["blobPath"]).download_blob().readall()
        content = json.loads(blob_data)
        return {**metadata, **content}
    except Exception as e:
        logger.error(f"Failed to get tool {tool_id}: {e}")
        return None

def list_user_tools(user_id: str) -> list:
    try:
        cosmos_container = get_tool_cosmos_container()
        query = "SELECT * FROM c WHERE c.userId = @userId AND c.type = 'SharePointTool' ORDER BY c.updatedAt DESC"
        return list(cosmos_container.query_items(query=query, parameters=[{"name": "@userId", "value": user_id}], partition_key=user_id))
    except Exception as e:
        logger.error(f"Failed to list tools: {e}")
        return []

def delete_sharepoint_tool(tool_id: str, user_id: str) -> bool:
    """
    ツールを削除する。部分失敗を防ぐため、CosmosDB を先に削除してから Blob を削除する。
    - CosmosDB 削除失敗 → Blob はそのまま残り、ツールは引き続き利用可能（安全な失敗）
    - CosmosDB 削除成功 → Blob 削除失敗 → Blob は孤立するが、ツールは一覧から消え、
      CosmosDB に不整合は残らない（許容可能な失敗）
    """
    blob_path = None
    try:
        cosmos_container = get_tool_cosmos_container()
        item = cosmos_container.read_item(item=tool_id, partition_key=user_id)
        blob_path = item.get("blobPath")

        # ① CosmosDB を先に削除する（失敗した場合は Blob も残り、ツールは安全に維持される）
        cosmos_container.delete_item(item=tool_id, partition_key=user_id)
    except Exception as e:
        logger.error(f"Delete failed (CosmosDB): tool_id={tool_id}, error={e}")
        return False

    # ② CosmosDB 削除成功後に Blob を削除する
    if blob_path:
        try:
            get_tool_blob_container_client().get_blob_client(blob_path).delete_blob()
        except Exception as e:
            # Blob 削除失敗は孤立 Blob として残るが、CosmosDB は既に消えているため
            # ツール一覧には表示されない。ログに記録して続行する。
            logger.error(f"Blob deletion failed (orphaned blob): tool_id={tool_id}, blob_path={blob_path}, error={e}")

    # 関連するインタラクション等の削除はコストがかかるため今回は省略（または別途バッチ処理）
    return True
    
def check_and_update_token_usage(tool_id: str, tokens_to_add: int, model_type: str = 'standard') -> bool:
    """
    ツール毎・モデル種別毎の日次トークン使用量をチェックし、上限以内であれば加算する。
    :param model_type: 'standard' or 'advanced'
    """
    try:
        container = get_tool_cosmos_container()
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # モデル種別に応じた上限を選択
        if model_type == 'advanced':
            limit = TOKEN_LIMIT_ADVANCED
        else:
            limit = TOKEN_LIMIT_STANDARD

        # モデル種別を含むUsage IDで独立カウント
        usage_id = f"token_usage_{tool_id}_{model_type}_{today}"

        # PartitionKey取得のためにツール検索
        query = "SELECT * FROM c WHERE c.id = @tid"
        items = list(container.query_items(query=query, parameters=[{"name": "@tid", "value": tool_id}], enable_cross_partition_query=True))
        if not items: return True
        user_id = items[0]['userId']

        try:
            usage_item = container.read_item(item=usage_id, partition_key=user_id)
        except Exception:
            usage_item = {
                "id": usage_id,
                "userId": user_id,
                "toolId": tool_id,
                "date": today,
                "modelType": model_type,
                "totalTokens": 0,
                "type": "TokenUsage"
            }

        if usage_item["totalTokens"] + tokens_to_add > limit:
            return False

        usage_item["totalTokens"] += tokens_to_add
        container.upsert_item(body=usage_item)
        return True
    except Exception as e:
        logger.error(f"Token Tracking Failed: {e}")
        return True

# ==============================================================================
#  【新規追加】ソーシャル・トップ画面用機能
# ==============================================================================

def toggle_interaction(user_id: str, tool_id: str, tool_author_id: str, interaction_type: str, action: str) -> bool:
    """
    いいね/お気に入りの切り替えを行う。
    :param interaction_type: 'like' | 'favorite'
    :param action: 'add' | 'remove'
    """
    container = get_tool_cosmos_container()
    interaction_id = f"{interaction_type}_{tool_id}"
    
    try:
        # 1. User Partition: インタラクション記録の作成/削除
        #    これにより「自分のお気に入り一覧」を高速に取得可能
        if action == 'add':
            interaction_item = {
                "id": interaction_id,
                "userId": user_id,
                "toolId": tool_id,
                "targetUserId": tool_author_id,
                "interactionType": interaction_type,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "type": "UserInteraction"
            }
            container.upsert_item(body=interaction_item)
        elif action == 'remove':
            try:
                container.delete_item(item=interaction_id, partition_key=user_id)
            except cosmos_exceptions.CosmosResourceNotFoundError:
                pass # 既に存在しない場合は無視

        # 2. Author Partition: 'like' の場合のみ、ツール自体のカウントを更新
        #    Cosmos DBの Partial Update (Patch) を使用
        if interaction_type == 'like':
            increment = 1 if action == 'add' else -1
            try:
                container.patch_item(
                    item=tool_id,
                    partition_key=tool_author_id,
                    patch_operations=[
                        {"op": "incr", "path": "/likeCount", "value": increment}
                    ]
                )
            except cosmos_exceptions.CosmosResourceNotFoundError:
                logger.warning(f"Like target tool not found: {tool_id}")
            except Exception as e:
                logger.warning(f"Failed to update like count: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Toggle interaction failed: {e}")
        return False

def get_user_favorite_tools(user_id: str) -> list:
    """ユーザーがお気に入り登録したツールの詳細リストを取得"""
    try:
        container = get_tool_cosmos_container()
        
        # 1. お気に入りID一覧を取得 (Single Partition Query)
        query_fav = "SELECT * FROM c WHERE c.userId = @userId AND c.interactionType = 'favorite'"
        fav_items = list(container.query_items(
            query=query_fav,
            parameters=[{"name": "@userId", "value": user_id}],
            partition_key=user_id
        ))
        
        if not fav_items:
            return []
            
        # 2. 各ツールの詳細を取得
        #    PartitionKeyが異なるため、それぞれ read_item で取得する
        tools = []
        for fav in fav_items:
            try:
                tool = container.read_item(item=fav['toolId'], partition_key=fav['targetUserId'])
                tools.append(tool)
            except cosmos_exceptions.CosmosResourceNotFoundError:
                continue # ツールが削除されている場合
            except Exception as e:
                logger.warning(f"Failed to fetch favorite tool {fav['toolId']}: {e}")
                
        return tools
    except Exception as e:
        logger.error(f"Failed to get favorite tools: {e}")
        return []

def get_tool_interaction_status(user_id: str, tool_id: str) -> dict:
    """指定ユーザーがそのツールに対して いいね/お気に入り しているか確認"""
    try:
        container = get_tool_cosmos_container()
        status = {"isLiked": False, "isFavorite": False}
        
        # IDが予測可能なので直接Readを試みる (RU節約)
        try:
            container.read_item(item=f"like_{tool_id}", partition_key=user_id)
            status["isLiked"] = True
        except cosmos_exceptions.CosmosResourceNotFoundError:
            pass
            
        try:
            container.read_item(item=f"favorite_{tool_id}", partition_key=user_id)
            status["isFavorite"] = True
        except cosmos_exceptions.CosmosResourceNotFoundError:
            pass
            
        return status
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {"isLiked": False, "isFavorite": False}

def get_public_tools(category: str = None, limit: int = 20, offset: int = 0) -> list:
    """
    公開設定されたツールを取得 (射影とページネーション対応)
    """
    try:
        container = get_tool_cosmos_container()
        
        # ★対策1-B: 射影 (Projection) - 必要な項目だけを取得してデータ量を減らす
        # description は一覧表示用。blobPathなどは一覧には不要なので除外。
        select_clause = """
            SELECT
                c.id,
                c.toolName,
                c.description,
                c.likeCount,
                c.viewCount,
                c.authorName,
                c.category,
                c.updatedAt,
                c.userId,
                c.type,
                c.isPublic,
                c.connectionSettings
        """
        
        query = f"{select_clause} FROM c WHERE c.type = 'SharePointTool' AND c.isPublic = true"
        params = []
        
        if category and category != "すべて":
            query += " AND c.category = @category"
            params.append({"name": "@category", "value": category})
            
        # ★対策1-A: ページネーション (OFFSET / LIMIT)
        query += " ORDER BY c.likeCount DESC OFFSET @offset LIMIT @limit"
        params.append({"name": "@offset", "value": offset})
        params.append({"name": "@limit", "value": limit})
        
        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        return items
    except Exception as e:
        logger.error(f"Failed to list public tools: {e}")
        return []

def list_all_tools_for_admin(
    search_query: str = None,
    author_name: str = None,
    date_from: str = None,
    date_to: str = None,
    sort_by: str = 'updatedAt',
    sort_order: str = 'DESC',
    limit: int = 50,
    offset: int = 0
) -> dict:
    """
    管理者向けに全ツールを検索・ページネーション・ソート付きで取得する。
    """
    try:
        container = get_tool_cosmos_container()
        
        # --- ソートキーの検証 (SQLインジェクション対策) ---
        allowed_sort_keys = ['toolName', 'authorName', 'createdAt', 'updatedAt', 'likeCount', 'viewCount']
        if sort_by not in allowed_sort_keys:
            sort_by = 'updatedAt'  # 不正な値の場合はデフォルトに戻す
        
        sort_order = 'ASC' if sort_order.upper() == 'ASC' else 'DESC'

        # 射影(Projection)
        select_clause = """
            SELECT c.id, c.toolName, c.authorName, c.createdAt, c.updatedAt,
                   c.likeCount, c.viewCount, c.isPublic, c.userId, c.connectionSettings
        """
        
        query = f"{select_clause} FROM c WHERE c.type = 'SharePointTool'"
        params = []
        
        # --- 動的な検索条件の構築 ---
        if search_query:
            query += " AND CONTAINS(c.toolName, @search_query, true)"
            params.append({"name": "@search_query", "value": search_query})
            
        if author_name:
            query += " AND CONTAINS(c.authorName, @author_name, true)"
            params.append({"name": "@author_name", "value": author_name})

        if date_from:
            query += " AND c.updatedAt >= @date_from"
            params.append({"name": "@date_from", "value": date_from})

        if date_to:
            # 日付の終わり(23:59:59)までを範囲に含める
            query += " AND c.updatedAt <= @date_to"
            params.append({"name": "@date_to", "value": date_to + "T23:59:59.999Z"})

        # 総件数取得クエリ
        count_query = query.replace(select_clause, "SELECT VALUE COUNT(1)")
        count_params = [p for p in params]
        
        total_count_result = list(container.query_items(
            query=count_query,
            parameters=count_params,
            enable_cross_partition_query=True
        ))
        total_count = total_count_result[0] if total_count_result else 0

        # --- ソートとページネーションをクエリに追加 ---
        query += f" ORDER BY c.{sort_by} {sort_order} OFFSET @offset LIMIT @limit"
        params.append({"name": "@offset", "value": offset})
        params.append({"name": "@limit", "value": limit})
        
        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        return {"tools": items, "totalCount": total_count}

    except Exception as e:
        logger.error(f"Failed to list all tools for admin: {e}", exc_info=True)
        return {"tools": [], "totalCount": 0}