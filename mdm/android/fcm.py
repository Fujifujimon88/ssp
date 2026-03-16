"""Firebase Cloud Messaging (FCM) プッシュ通知送信 - HTTP v1 API

Android DPC APKを起動してコマンドキューのポーリングを促すプッシュ通知を送信する。
FCM HTTP v1 API（OAuth2 Bearer トークン認証）を使用。

設定（.env）:
    FCM_PROJECT_ID          - Firebase プロジェクトID（例: my-project-12345）
    FCM_SERVICE_ACCOUNT_PATH - サービスアカウントJSONファイルのパス
"""
import asyncio
import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

_FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
_token_cache: dict = {"token": None, "expiry": 0.0}


async def _get_access_token() -> str:
    """キャッシュされたアクセストークンを返す。期限切れなら非同期で更新する。"""
    import google.auth.transport.requests
    from google.oauth2 import service_account

    now = time.time()
    if _token_cache["token"] and _token_cache["expiry"] > now + 60:
        return _token_cache["token"]  # type: ignore[return-value]

    def _refresh() -> tuple[str, float]:
        creds = service_account.Credentials.from_service_account_file(
            settings.fcm_service_account_path,
            scopes=_FCM_SCOPES,
        )
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        return creds.token, creds.expiry.timestamp()

    loop = asyncio.get_event_loop()
    token, expiry = await loop.run_in_executor(None, _refresh)
    _token_cache["token"] = token
    _token_cache["expiry"] = expiry
    return token


async def _post_to_fcm(message: dict) -> dict:
    """FCM v1 エンドポイントへPOSTしてレスポンスJSONを返す。失敗時は例外を送出する。"""
    token = await _get_access_token()
    url = f"https://fcm.googleapis.com/v1/projects/{settings.fcm_project_id}/messages:send"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={"message": message},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


def _is_configured() -> bool:
    return bool(settings.fcm_project_id and settings.fcm_service_account_path)


async def send_command_ping(fcm_token: str, device_id: str) -> bool:
    """
    DPC APKにコマンドキューの確認を促すサイレントプッシュを送信する。

    Args:
        fcm_token: デバイスのFCM登録トークン
        device_id: Android ID（ログ用）

    Returns:
        True: 送信成功 / False: スキップ or エラー
    """
    if not _is_configured():
        logger.debug("FCM: fcm_project_id / fcm_service_account_path 未設定のためスキップ")
        return False

    message = {
        "token": fcm_token,
        "data": {"action": "POLL_COMMANDS", "device_id": device_id},
        "android": {"priority": "HIGH"},
    }

    try:
        await _post_to_fcm(message)
        logger.info(f"FCM: command ping sent | device={device_id[:8]}...")
        return True
    except Exception as e:
        logger.warning(f"FCM: error | device={device_id[:8]}... | {e}")
        return False


async def send_notification(fcm_token: str, title: str, body: str, data: dict | None = None) -> bool:
    """
    ユーザーに表示するプッシュ通知を送信する（広告・クーポン告知等）。
    """
    if not _is_configured():
        return False

    message = {
        "token": fcm_token,
        "notification": {"title": title, "body": body},
        "data": data or {},
        "android": {"priority": "HIGH"},
    }

    try:
        await _post_to_fcm(message)
        return True
    except Exception as e:
        logger.warning(f"FCM: send_notification error | {e}")
        return False
