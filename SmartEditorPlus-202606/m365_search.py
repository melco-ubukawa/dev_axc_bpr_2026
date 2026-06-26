import logging
import json
import re
import uuid
import mimetypes
import os
from urllib.parse import quote

import requests
from flask import Blueprint, request, jsonify
from flask import current_app

from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)

m365_search_bp = Blueprint('m365_search', __name__)

def normalize_search_result(hit):
    """Microsoft Graph APIの検索結果を統一された形式に正規化します。"""
    resource = hit.get("resource", {}) or {}
    if not resource:
        return None

    base_result = {
        "id": resource.get("id"),
        "hitId": hit.get("hitId"),
        "snippet": hit.get("summary", "") or "",  # ← ここは既にOK
        "url": resource.get("webLink") or resource.get("webUrl"),
        "date": resource.get("createdDateTime"),
        "rawData": resource
    }

    hit_type = (resource.get("@odata.type") or "").lower()

    def html_to_text(h: str) -> str:
        return re.sub(r"<[^>]+?>", "", h or "").strip()

    if "chatmessage" in hit_type:
        # 1) 著者名（可能なら）
        from_user = (resource.get("from") or {}).get("user") or {}
        author_name = from_user.get("displayName") or "不明なユーザー"

        # 2) タイトル: hit['summary']（ハイライト付き）優先、なければ本文から生成
        hit_summary = hit.get("summary") or ""
        body_preview = html_to_text(((resource.get("body") or {}).get("content")) or "")

        # 優先順位: hit.summary → body.content → 著者ベースのフォールバック
        title_src = hit_summary or body_preview
        title_text = html_to_text(title_src).replace("\n", " ").strip()
        if title_text:
            # 長すぎる場合は適度にカット
            title = title_text[:80] + ("…" if len(title_text) > 80 else "")
        else:
            title = f"{author_name}からのメッセージ"

        base_result.update({
            "type": "teams",
            "title": title,
            "author": author_name,
        })
        return base_result

    elif "message" in hit_type:
        base_result.update({
            "type": "mail",
            "title": resource.get("subject", "件名なし"),
            "author": (resource.get("sender") or {}).get("emailAddress", {}).get("name", "不明な差出人"),
        })
        return base_result

    elif "driveitem" in hit_type:
        base_result.update({
            "type": "file",
            "title": resource.get("name", "名称未設定ファイル"),
            "author": ((resource.get("createdBy") or {}).get("user") or {}).get("displayName", "不明な作成者"),
        })
        return base_result

    return None


@m365_search_bp.route('/api/m365/search', methods=['POST'])
def search_m365():
    """M365横断検索。App Service認証が提供するトークンを使用。"""
    access_token = request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。App Serviceの認証設定を確認してください。"}), 401

    try:
        data = request.get_json(silent=True) or {}
        query = data.get('query')
        targets = data.get('targets', {})

        if not query:
            return jsonify({"success": False, "error": "検索クエリがありません。"}), 400

        search_endpoint = "https://graph.microsoft.com/v1.0/search/query"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        all_hits = []

        entity_map = {
            "mail": "message",
            "files": "driveItem",
            "teams": "chatMessage"
        }

        for target_key, entity_type in entity_map.items():
            if targets.get(target_key):
                logger.info(f"Searching for entity type: {entity_type} with query: '{query}'")
                request_body = {
                    "requests": [
                        {
                            "entityTypes": [entity_type],
                            "query": {"queryString": query},
                            "from": 0,
                            "size": 25
                        }
                    ]
                }

                response = requests.post(search_endpoint, headers=headers, json=request_body)
                response.raise_for_status()
                response_data = response.json()

                hits_containers = response_data.get("value", [{}])[0].get("hitsContainers", [])
                if hits_containers:
                    search_hits = hits_containers[0].get("hits", [])
                    all_hits.extend(search_hits)
                    logger.info(f"Found {len(search_hits)} hits for entity type: {entity_type}")

        normalized_results = [result for hit in all_hits if (result := normalize_search_result(hit)) is not None]
        # 日付降順
        normalized_results.sort(key=lambda x: x.get('date', ''), reverse=True)

        return jsonify({"success": True, "results": normalized_results})

    except requests.exceptions.HTTPError as e:
        logger.error(f"Graph API request failed: {e.response.status_code} - {e.response.text}")
        try:
            error_details = e.response.json().get("error", {}).get("message", "不明なエラー")
        except json.JSONDecodeError:
            error_details = e.response.text
        return jsonify({"success": False, "error": f"Microsoft Graph APIエラー: {error_details}"}), e.response.status_code
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify({"success": False, "error": "サーバー内部で予期せぬエラーが発生しました。"}), 500


@m365_search_bp.route('/api/m365/get_item_content', methods=['POST'])
def get_m365_item_content():
    """指定されたM365アイテムの本文やコンテンツを取得する。"""
    access_token = request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    headers = {"Authorization": f"Bearer {access_token}"}
    data = request.get_json(silent=True) or {}
    item_type = data.get('type')
    item_id = data.get('id')
    raw_data = data.get('rawData', {}) or {}

    if not item_type or not item_id:
        return jsonify({"success": False, "error": "アイテムの種類またはIDが不足しています。"}), 400

    try:
        content = ""
        item_name = raw_data.get('name') or raw_data.get('subject', '不明なアイテム')

        if item_type == 'mail':
            # まず /me で取得、失敗時に送信者のメールボックス等でフォールバック
            safe_item_id = quote(str(item_id), safe='')  # 完全エンコード
            mail_headers = headers.copy()
            mail_headers['Prefer'] = 'outlook.body-content-type="text"'

            def fetch_mail_by_path(path):
                resp = requests.get(f"https://graph.microsoft.com/v1.0/{path}/{safe_item_id}?$select=body,subject", headers=mail_headers)
                if resp.status_code == 200:
                    j = resp.json()
                    return j.get("body", {}).get("content", ""), j.get("subject", item_name)
                resp.raise_for_status()
                return "", item_name

            try:
                content, subject = fetch_mail_by_path("me/messages")
                item_name = subject
            except requests.exceptions.HTTPError as e1:
                # フォールバック: 送信者などのUPNを用いて /users/{upn}/messages/{id} を試す
                upn_candidates = []

                sender = (raw_data.get('sender') or raw_data.get('from') or {}).get('emailAddress', {}).get('address')
                if sender:
                    upn_candidates.append(sender)

                # 必要なら here: 受信者や自分自身のUPNを追加するなど拡張可能

                fetched = False
                for upn in upn_candidates:
                    try:
                        u = f"users/{quote(upn, safe='')}/messages"
                        content, subject = fetch_mail_by_path(u)
                        item_name = subject
                        fetched = True
                        break
                    except requests.exceptions.HTTPError:
                        continue

                if not fetched:
                    # 取れない場合は元のエラーを返す
                    raise e1

        elif item_type == 'teams':
            messages = []
            
            channel_info = raw_data.get('channelIdentity')
            team_id = channel_info.get('teamId') if channel_info else None
            channel_id = channel_info.get('channelId') if channel_info else None
            chat_id = raw_data.get('chatId')

            if team_id and channel_id:
                # 【チャネルメッセージの場合】
                logger.info(f"Fetching Teams channel messages for teamId: {team_id}, channelId: {channel_id}")
                safe_team_id = quote(team_id, safe='')
                safe_channel_id = quote(channel_id, safe='')
                list_url = f"https://graph.microsoft.com/v1.0/teams/{safe_team_id}/channels/{safe_channel_id}/messages?$top=30"
                resp = requests.get(list_url, headers=headers)
                resp.raise_for_status()
                messages = resp.json().get('value', [])
            elif chat_id:
                # 【1:1またはグループチャットの場合】
                logger.info(f"Fetching Teams chat messages for chatId: {chat_id}")
                safe_chat_id = quote(chat_id, safe='')
                list_url = f"https://graph.microsoft.com/v1.0/chats/{safe_chat_id}/messages?$top=30"
                resp = requests.get(list_url, headers=headers)
                resp.raise_for_status()
                messages = resp.json().get('value', [])
            else:
                # どちらのIDも見つからなかった場合
                raise ValueError("Teamsメッセージの取得に必要な情報(chatId または teamId/channelId)が検索結果に含まれていません。")

            # createdDateTime 昇順にソートして、発言者名+時刻+本文（HTML除去）で整形
            def html_to_text(h):
                return re.sub('<[^<]+?>', '', h or '').strip()

            messages.sort(key=lambda x: x.get('createdDateTime') or '')
            lines = []
            for msg in messages:
                b = msg.get('body', {}) or {}
                content_text = html_to_text(b.get('content', ''))
                if not content_text:
                    continue
                sender = (msg.get('from', {}) or {}).get('user', {}) or {}
                name = sender.get('displayName') or '不明なユーザー'
                ts = msg.get('createdDateTime') or ''
                lines.append(f"[{ts}] {name}: {content_text}")

            content = "\n".join(lines)
            item_name = raw_data.get('summary') or item_name

        elif item_type == 'file':
            return jsonify({"success": False, "error": "ファイルの本文取得は未対応です。AIで処理から添付準備を実行してください。"}), 400

        else:
            return jsonify({"success": False, "error": f"未対応のアイテム種類です: {item_type}"}), 400

        return jsonify({"success": True, "content": content, "title": item_name})

    except requests.exceptions.HTTPError as e:
        logger.error(f"Graph API request for content failed: {e.response.status_code} - {e.response.text}")
        try:
            error_details = e.response.json().get("error", {}).get("message", "不明なエラー")
        except json.JSONDecodeError:
            error_details = e.response.text
        return jsonify({"success": False, "error": f"Microsoft Graph APIエラー: {error_details}"}), e.response.status_code
    except Exception as e:
        logger.error(f"An unexpected error occurred while getting content: {e}", exc_info=True)
        return jsonify({"success": False, "error": "サーバー内部で予期せぬエラーが発生しました。"}), 500


def get_blob_service_client():
    """設定からBlobServiceClientを取得します。"""
    connect_str = current_app.config.get('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        raise ValueError("Azure Storageの接続文字列が設定されていません。")
    return BlobServiceClient.from_connection_string(connect_str)


@m365_search_bp.route('/api/m365/prepare_file_for_chat', methods=['POST'])
def prepare_file_for_chat():
    """
    OneDrive/SharePoint上のファイルをダウンロードし、チャット添付用にAzure Blobにアップロードする。(複数ロケーション対応)
    成功時レスポンス:
    {
      success: true,
      blobName: str,
      fileName: str,
      contentType: str,
      size: int
    }
    """
    access_token = request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    headers = {"Authorization": f"Bearer {access_token}"}
    data = request.get_json(silent=True) or {}
    item_id = data.get('id')
    item_name = data.get('name')
    raw_data = data.get('rawData', {}) or {}

    if not item_id or not item_name:
        return jsonify({"success": False, "error": "ファイルIDまたはファイル名が不足しています。"}), 400

    try:
        parent_reference = raw_data.get('parentReference', {}) or {}
        drive_id = parent_reference.get('driveId')
        safe_item_id = quote(str(item_id), safe='')

        if drive_id:
            # SharePoint/Teams の場合
            safe_drive_id = quote(drive_id, safe='')
            download_url = f"https://graph.microsoft.com/v1.0/drives/{safe_drive_id}/items/{safe_item_id}/content"
            logger.info(f"Constructed SharePoint/Teams file download URL: {download_url}")
        else:
            # OneDrive (自分)
            download_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{safe_item_id}/content"
            logger.info(f"Constructed OneDrive file download URL: {download_url}")

        # 1) ダウンロード
        logger.info(f"Downloading file '{item_name}' (ID: {item_id}) from Graph API...")
        resp = requests.get(download_url, headers=headers)
        resp.raise_for_status()
        file_content = resp.content
        size = len(file_content)

        # 2) Blob Storageへアップロード
        blob_service = get_blob_service_client()
        container_name = current_app.config.get('AZURE_STORAGE_CONTAINER_NAME', 'attachments')
        container_client = blob_service.get_container_client(container_name)
        try:
            container_client.create_container()
        except Exception:
            # 既に存在する場合などは無視
            pass

        # ファイル名/コンテンツタイプ
        file_name = item_name
        content_type = mimetypes.guess_type(file_name)[0] or 'application/octet-stream'

        # 衝突しないBlob名
        safe_basename = os.path.basename(file_name)
        blob_name = f"m365/{uuid.uuid4().hex}_{safe_basename}"

        blob_client = container_client.get_blob_client(blob_name)
        content_settings = ContentSettings(content_type=content_type)

        logger.info(f"Uploading to blob '{blob_name}' (container: {container_name}, size: {size})")
        blob_client.upload_blob(
            file_content,
            overwrite=True,
            content_settings=content_settings
        )

        return jsonify({
            "success": True,
            "blobName": blob_name,
            "fileName": file_name,
            "contentType": content_type,
            "size": size
        })

    except requests.exceptions.HTTPError as e:
        logger.error(f"Graph API file download failed: {e.response.status_code} - {e.response.text}")
        error_details = e.response.text
        if e.response.status_code == 404:
            error_details = "指定されたファイルが見つかりませんでした。アクセス権がないか、ファイルが移動または削除された可能性があります。"
        else:
            try:
                error_details = e.response.json().get("error", {}).get("message", "不明なエラー")
            except json.JSONDecodeError:
                pass
        return jsonify({"success": False, "error": f"ファイルのダウンロードに失敗しました: {error_details}"}), e.response.status_code
    except Exception as e:
        logger.error(f"An unexpected error occurred during file preparation: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"サーバー内部エラー: {str(e)}"}), 500
    
@m365_search_bp.route('/api/m365/prepare_mail_for_chat', methods=['POST'])
def prepare_mail_for_chat():
    access_token = request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    headers = {"Authorization": f"Bearer {access_token}"}
    data = request.get_json(silent=True) or {}
    
    # hitId を優先的に使用 (Graph Search APIで推奨)
    item_id_to_use = data.get('id') or data.get('hitId')
    raw_data = data.get('rawData', {}) or {}
    if not item_id_to_use:
        return jsonify({"success": False, "error": "メールIDが不足しています。"}), 400

    try:
        safe_item_id = quote(str(item_id_to_use), safe='')
        
        # ファイル名を件名から生成
        subject = (raw_data.get('subject') or 'mail').strip()
        safe_subject = re.sub(r'[\\/*?:"<>|]+', '_', subject)[:150] or 'mail'
        file_name = f"{safe_subject}.eml"
        content_type = 'message/rfc822'

        # MIMEコンテンツを取得する内部関数
        def fetch_mime_by_path(path):
            url = f"https://graph.microsoft.com/v1.0/{path}/{safe_item_id}/$value"
            logger.info(f"Attempting to fetch mail MIME content from: {url}")
            resp = requests.get(url, headers=headers, stream=True)
            resp.raise_for_status()
            return b"".join(resp.iter_content())

        try:
            # まずは /me/messages で試す
            mime_bytes = fetch_mime_by_path("me/messages")
        except requests.exceptions.HTTPError as e1:
            # 失敗した場合、送信者のメールボックスで試す
            sender = (raw_data.get('sender') or raw_data.get('from') or {}).get('emailAddress', {}).get('address')
            fetched = False
            if sender:
                try:
                    upn = quote(sender, safe='')
                    mime_bytes = fetch_mime_by_path(f"users/{upn}/messages")
                    fetched = True
                except requests.exceptions.HTTPError:
                    pass
            if not fetched:
                raise e1

        # Blobへアップロード
        blob_service = get_blob_service_client()
        container_name = current_app.config.get('AZURE_STORAGE_CONTAINER_NAME', 'attachments')
        container_client = blob_service.get_container_client(container_name)
        try:
            container_client.create_container()
        except Exception:
            pass

        blob_name = f"m365/{uuid.uuid4().hex}_{os.path.basename(file_name)}"
        blob_client = container_client.get_blob_client(blob_name)
        content_settings = ContentSettings(content_type=content_type)
        blob_client.upload_blob(mime_bytes, overwrite=True, content_settings=content_settings)

        return jsonify({
            "success": True,
            "blobName": blob_name,
            "fileName": file_name,
            "contentType": content_type,
            "size": len(mime_bytes)
        })
    except requests.exceptions.HTTPError as e:
        logger.error(f"Graph API mail MIME download failed: {e.response.status_code} - {e.response.text}")
        try:
            error_details = e.response.json().get("error", {}).get("message", "不明なエラー")
        except json.JSONDecodeError:
            error_details = e.response.text
        return jsonify({"success": False, "error": f"メールのダウンロードに失敗しました: {error_details}"}), e.response.status_code
    except Exception as e:
        logger.error(f"Unexpected error in prepare_mail_for_chat: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"サーバー内部エラー: {str(e)}"}), 500