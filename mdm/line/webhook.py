"""LINE Webhookハンドラー

LINEからのイベント（友だち追加・メッセージ等）を受信し、
デバイスDBへのLINE User ID紐付けとエル投げ連携を行う。
"""
import hashlib
import hmac
import json
import logging
from base64 import b64decode

from config import settings

logger = logging.getLogger(__name__)


def verify_signature(body: bytes, signature: str) -> bool:
    """LINE Webhook署名検証（HMAC-SHA256）"""
    if not settings.line_channel_secret:
        logger.debug("LINE: channel_secret未設定のため署名検証スキップ")
        return True

    expected = hmac.new(
        settings.line_channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()

    try:
        received = b64decode(signature)
    except Exception:
        return False

    return hmac.compare_digest(expected, received)


def parse_follow_events(payload: dict) -> list[str]:
    """
    LINEイベントペイロードから「友だち追加（follow）」したユーザーのIDリストを返す
    """
    user_ids = []
    for event in payload.get("events", []):
        if event.get("type") == "follow":
            uid = event.get("source", {}).get("userId")
            if uid:
                user_ids.append(uid)
    return user_ids
