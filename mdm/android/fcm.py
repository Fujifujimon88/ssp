"""Firebase Cloud Messaging (FCM) プッシュ通知送信

Android DPC APKを起動してコマンドキューのポーリングを促すプッシュ通知を送信する。
FCM Legacy HTTP API（v1 移行前の簡易実装）を使用。
"""
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

FCM_SEND_URL = "https://fcm.googleapis.com/fcm/send"


async def _post_to_fcm(payload: dict) -> dict:
    """FCMエンドポイントへPOSTしてレスポンスJSONを返す。失敗時は例外を送出する。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            FCM_SEND_URL,
            json=payload,
            headers={
                "Authorization": f"key={settings.fcm_server_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_command_ping(fcm_token: str, device_id: str) -> bool:
    """
    DPC APKにコマンドキューの確認を促すサイレントプッシュを送信する。

    Args:
        fcm_token: デバイスのFCM登録トークン
        device_id: Android ID（ログ用）

    Returns:
        True: 送信成功 / False: スキップ or エラー
    """
    if not settings.fcm_server_key:
        logger.debug("FCM: fcm_server_key未設定のためスキップ")
        return False

    payload = {
        "to": fcm_token,
        "data": {"action": "POLL_COMMANDS", "device_id": device_id},
        "android": {"priority": "high"},
    }

    try:
        result = await _post_to_fcm(payload)
        if result.get("failure", 0) > 0:
            logger.warning(f"FCM: failure in response | device={device_id[:8]}... | {result}")
            return False
        logger.info(f"FCM: command ping sent | device={device_id[:8]}...")
        return True
    except Exception as e:
        logger.warning(f"FCM: error | device={device_id[:8]}... | {e}")
        return False


async def send_notification(fcm_token: str, title: str, body: str, data: dict | None = None) -> bool:
    """
    ユーザーに表示するプッシュ通知を送信する（広告・クーポン告知等）。
    """
    if not settings.fcm_server_key:
        return False

    payload = {
        "to": fcm_token,
        "notification": {"title": title, "body": body},
        "data": data or {},
        "android": {"priority": "high"},
    }

    try:
        await _post_to_fcm(payload)
        return True
    except Exception as e:
        logger.warning(f"FCM: send_notification error | {e}")
        return False
