import logging
import uuid
import json
from datetime import datetime, timezone
import config

# Cosmos DB操作用モジュール
from .cosmos_db import (
    create_design_project,
    get_design_project,
    update_design_project
)

# Blob Storage操作用モジュール
from .storage import (
    upload_json_to_blob,
    download_json_from_blob,
    delete_blob
)

logger = logging.getLogger(__name__)

class DesignProjectService:
    """
    設計アシスタントプロジェクトのデータアクセス層。
    Cosmos DB (メタデータ) と Blob Storage (実データ: ドキュメント本文、チャット履歴) の
    ハイブリッド構成を隠蔽して管理する。
    """

    @staticmethod
    def create_project(user_id: str, project_name: str, developer_level: str, initial_data: dict) -> dict | None:
        """
        新規プロジェクトを作成する。
        - 実データ(documents, diagrams, chatHistory等) -> Blob Storage
        - メタデータ(id, name, status, blobPath) -> Cosmos DB
        """
        try:
            project_id = f"sdp_{uuid.uuid4().hex}"
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # Blobに保存するパス (user_id/project_id/latest.json)
            blob_path = f"{user_id}/{project_id}/latest.json"
            container_name = config.AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME

            # 1. 実データ (Heavy Data) の構築
            # ここにはドキュメント本文や図解コード、チャット履歴などの容量が大きいデータを含める
            full_project_data = {
                "id": project_id,
                "userId": user_id,
                "projectName": project_name,
                "developerLevel": developer_level,
                "documents": initial_data.get("documents", {}),
                "diagrams": initial_data.get("diagrams", {}),
                "infographics": initial_data.get("infographics", {}),  # ★追加: インフォグラフィック
                "chatHistory": initial_data.get("chatHistory", []),
                "contextFiles": initial_data.get("contextFiles", []),
                "updatedAt": now_iso
            }

            # 2. Blobへのアップロード
            if not upload_json_to_blob(full_project_data, blob_path, container_name):
                logger.error(f"Failed to upload initial project data to blob: {blob_path}")
                return None

            # 3. メタデータ (Lightweight Data) の構築
            # 一覧表示や検索に必要な軽量なデータのみをCosmos DBに保存
            metadata_doc = {
                "id": project_id,
                "userId": user_id,
                "type": "SoftwareDesignProject",
                "projectName": project_name,
                "developerLevel": developer_level, # '1' (Concept) or '2' (Detailed)
                "phase": "init",                   # 現在のフェーズ
                "status": "active",
                "blobPath": blob_path,             # Blobへのポインタ
                "containerName": container_name,
                "createdAt": now_iso,
                "updatedAt": now_iso
            }

            # 4. Cosmos DBへの保存
            created_meta = create_design_project(metadata_doc)
            if not created_meta:
                logger.error("Failed to create project metadata in Cosmos DB.")
                return None
            
            logger.info(f"Design project '{project_id}' created successfully (Hybrid).")
            
            # フロントエンドにはマージ済みの全データを返す
            merged_response = full_project_data.copy()
            merged_response.update(metadata_doc)
            return merged_response

        except Exception as e:
            logger.error(f"Error creating design project: {e}", exc_info=True)
            return None

    @staticmethod
    def get_project(project_id: str, user_id: str) -> dict | None:
        """
        プロジェクトを取得する。
        Cosmos DBからメタデータを取得し、それを元にBlobから実データを取得してマージする。
        """
        try:
            # 1. Cosmos DBからメタデータ取得
            metadata = get_design_project(project_id, user_id)
            if not metadata:
                logger.warning(f"Project '{project_id}' metadata not found in Cosmos DB.")
                return None
            
            blob_path = metadata.get("blobPath")
            container_name = metadata.get("containerName", config.AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME)

            if not blob_path:
                # 異常系: メタデータはあるがBlobパスがない場合（古いデータ等）
                logger.warning(f"Project '{project_id}' has no blobPath. Returning metadata only.")
                return metadata

            # 2. Blobから実データ取得
            blob_data = download_json_from_blob(blob_path, container_name)
            
            if not blob_data:
                logger.error(f"Project blob not found at '{blob_path}' but metadata exists.")
                # 実データが見つからない場合はメタデータのみを返す（エラーにはしない）
                return metadata

            # 3. マージ
            merged_data = blob_data.copy()
            merged_data.update(metadata)
            
            return merged_data

        except Exception as e:
            logger.error(f"Error retrieving design project '{project_id}': {e}", exc_info=True)
            return None

    @staticmethod
    def update_project(project_id: str, user_id: str, updates: dict) -> dict | None:
        """
        プロジェクトを更新する。
        - 重量データ(documents, diagrams, chatHistory等)が含まれる場合はBlobを更新。
        - メタデータ(phase, status等)はCosmos DBも更新。
        """
        try:
            # 1. 現在のメタデータを取得（Blobパスを知るため）
            metadata = get_design_project(project_id, user_id)
            if not metadata:
                logger.warning(f"Project '{project_id}' not found for update.")
                return None

            now_iso = datetime.now(timezone.utc).isoformat()
            
            # 更新データがBlob保存対象（重量データ）を含むかチェック
            # ★ "infographics" を追加
            heavy_keys = ["documents", "diagrams", "infographics", "chatHistory", "contextFiles"]
            needs_blob_update = any(key in updates for key in heavy_keys)
            
            updated_blob_data = {}

            # 2. Blobデータの更新 (必要な場合)
            if needs_blob_update:
                blob_path = metadata.get("blobPath")
                container_name = metadata.get("containerName", config.AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME)
                
                if blob_path:
                    # 現在のBlobデータを取得
                    current_blob_data = download_json_from_blob(blob_path, container_name)
                    if current_blob_data:
                        # データをマージ (Deep Merge for specific keys)
                        for key, value in updates.items():
                            # ★ "infographics" を追加
                            if key in ["documents", "diagrams", "infographics"] and isinstance(value, dict):
                                # 既存の辞書がある場合はマージ、なければ新規作成
                                if key not in current_blob_data or not isinstance(current_blob_data[key], dict):
                                    current_blob_data[key] = {}
                                current_blob_data[key].update(value)
                            else:
                                # その他のフィールドは上書き
                                current_blob_data[key] = value
                        
                        current_blob_data["updatedAt"] = now_iso
                        
                        # Blobへ上書きアップロード
                        if upload_json_to_blob(current_blob_data, blob_path, container_name):
                            updated_blob_data = current_blob_data
                        else:
                            raise Exception("Failed to update project blob data.")
                    else:
                        raise Exception("Original blob data not found during update.")
                else:
                    raise Exception("Blob path not defined in metadata.")

            # 3. Cosmos DB (メタデータ) の更新
            meta_updates = {k: v for k, v in updates.items() if k not in heavy_keys}
            meta_updates["updatedAt"] = now_iso
            
            updated_meta = update_design_project(project_id, user_id, meta_updates)
            
            if updated_meta:
                logger.info(f"Project '{project_id}' updated. (Blob update: {needs_blob_update})")
                
                result = updated_meta.copy()
                if updated_blob_data:
                    result.update(updated_blob_data)
                return result
            else:
                logger.error("Failed to update project metadata.")
                return None

        except Exception as e:
            logger.error(f"Error updating project '{project_id}': {e}", exc_info=True)
            return None
        
    @staticmethod
    def delete_project(project_id: str, user_id: str) -> bool:
        """
        プロジェクトを削除する。
        """
        try:
            # 1. メタデータを取得してBlobパスを特定
            metadata = get_design_project(project_id, user_id)
            if not metadata:
                logger.warning(f"Project '{project_id}' not found for deletion.")
                return False

            blob_path = metadata.get("blobPath")
            # container_name = metadata.get("containerName", config.AZURE_STORAGE_DESIGN_PROJECTS_CONTAINER_NAME)
            
            # 2. Blobデータの削除
            if blob_path:
                delete_blob(blob_path)
            
            # 3. メタデータの削除
            from .cosmos_db import delete_design_project
            if delete_design_project(project_id, user_id):
                logger.info(f"Project '{project_id}' deleted successfully.")
                return True
            else:
                logger.error("Failed to delete project metadata.")
                return False

        except Exception as e:
            logger.error(f"Error deleting project '{project_id}': {e}", exc_info=True)
            return False