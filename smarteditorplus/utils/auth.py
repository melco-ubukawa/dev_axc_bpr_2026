# .utils/api_auth.py
# Microsoft Graph API へのリクエストに必要な認証ヘッダーの取得と、
# 共通のエラーハンドリングを行うユーティリティ。
import base64
import json
import logging
import requests
from flask import jsonify, request

# Microsoft Graph API の v1.0 エンドポイント
GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"
logger = logging.getLogger(__name__)

def get_auth_header(current_request):
    """
    FlaskのリクエストオブジェクトからAzure App Serviceが提供する
    AADアクセストークンを取得し、Graph API用の認証ヘッダーを作成します。

    Args:
        current_request: Flask の request オブジェクト。

    Returns:
        認証ヘッダーを含む辞書。トークンがない場合はNone。
    """
    # App Service 認証が有効な場合、このヘッダーにトークンが設定される
    access_token = current_request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return None
    
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

def handle_graph_http_error(e: requests.exceptions.HTTPError):
    """
    requests.HTTPErrorを捕捉し、Graph APIからのエラーを解析して、
    適切なJSONレスポンスとステータスコードを返します。
    特に、トークン失効エラー（401 InvalidAuthenticationToken）を検知し、
    フロントエンドが再試行できるように特別なペイロードを返します。

    Args:
        e: requests.exceptions.HTTPError のインスタンス。

    Returns:
        Flask の Response オブジェクト (tuple)。
    """
    status = e.response.status_code if e.response is not None else 500
    text = e.response.text if e.response is not None else str(e)
    
    # 401 Unauthorized かつエラーメッセージに 'InvalidAuthenticationToken' が含まれるかチェック
    token_expired = (status == 401 and "InvalidAuthenticationToken" in text)
    
    payload = {
        "success": False,
        "error": f"Graph APIエラー: {text}",
    }
    
    # トークン失効の場合、フロントエンドに再試行を促すためのフラグを追加
    if token_expired:
        payload["tokenExpired"] = True
    
    # トークン失効時は401、それ以外のGraph APIエラーは元のステータスコードを返す
    response_status = 401 if token_expired else status
    
    return jsonify(payload), response_status

def get_entra_user_info(current_request):
    """
    リクエストヘッダーからEntra IDのユーザー情報を抽出し、辞書として返す。
    （routes.pyからこの共通ユーティリティファイルに移動）
    """
    user_id = current_request.headers.get("X-MS-CLIENT-PRINCIPAL-ID")
    principal_name = current_request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
    user_email = principal_name
    user_name = principal_name

    client_principal_b64 = current_request.headers.get("X-MS-CLIENT-PRINCIPAL")

    if client_principal_b64:
        try:
            client_principal_json = base64.b64decode(client_principal_b64).decode('utf-8')
            client_principal_data = json.loads(client_principal_json)
            
            claims = client_principal_data.get("claims", [])
            for claim in claims:
                claim_type = claim.get("typ", "").lower()
                claim_value = claim.get("val")

                if claim_type == "name":
                    user_name = claim_value
                elif claim_type == "email":
                    user_email = claim_value
                elif claim_type == "preferred_username" and "@" in claim_value and (user_email == principal_name or not user_email):
                    user_email = claim_value
                elif claim_type in ["http://schemas.microsoft.com/identity/claims/objectidentifier", "oid"]:
                    if not user_id:
                        user_id = claim_value
                elif claim_type == "sub":
                     if not user_id:
                        user_id = claim_value
            
            if user_email == principal_name and principal_name and "@" not in principal_name:
                user_email = None 

        except Exception as e:
            logger.error(f"get_entra_user_info - Failed to parse X-MS-CLIENT-PRINCIPAL header: {e}", exc_info=True)

    user_info_result = {
        "userId": user_id or "local-dev-user",
        "userName": user_name or "Local Dev User",
        "userEmail": user_email or "local-dev@example.com"
    }
    return user_info_result