"""APNs（Apple Push Notification Service）MDM Push送信

iOSデバイスへMDMサーバーへのチェックインを促すサイレントプッシュを送信する。
MDMプッシュはJWT認証ではなく、Apple発行のプッシュ証明書を使用する。

セットアップ手順：
1. Apple Push Certificates Portal (identity.apple.com/pushcert) でMDM証明書を発行
2. 証明書(.pem) + 秘密鍵(.pem) を取得
3. .env に apns_cert_path, apns_key_path, apns_topic を設定

APNs HTTP/2 エンドポイント：
  本番: https://api.push.apple.com/3/device/{push_token}
  SB:   https://api.sandbox.push.apple.com/3/device/{push_token}
"""
import json
import logging
import ssl
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger(__name__)

_APNS_HOST_PROD = "https://api.push.apple.com"
_APNS_HOST_SANDBOX = "https://api.sandbox.push.apple.com"


def _apns_host() -> str:
    return _APNS_HOST_PROD if settings.apns_production else _APNS_HOST_SANDBOX


def _build_ssl_context() -> ssl.SSLContext | None:
    """APNs用SSLコンテキスト（クライアント証明書認証）"""
    if not settings.apns_cert_path or not settings.apns_key_path:
        return None

    cert_path = Path(settings.apns_cert_path)
    key_path = Path(settings.apns_key_path)

    if not cert_path.exists() or not key_path.exists():
        logger.warning("APNs: cert/key file not found")
        return None

    ctx = ssl.create_default_context()
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


async def send_mdm_push(push_token: str, push_magic: str) -> bool:
    """
    iOSデバイスへMDMチェックインを促すサイレントプッシュを送信する。
    デバイスはこのプッシュを受け取るとMDMサーバーへ接続してコマンドを取得する。

    Args:
        push_token: デバイスのAPNsプッシュトークン（チェックイン時に取得）
        push_magic:  デバイスのPushMagic文字列（チェックイン時に取得）

    Returns:
        True: 送信成功
    """
    if not settings.apns_topic:
        logger.debug("APNs: apns_topic未設定のためスキップ")
        return False

    ssl_ctx = _build_ssl_context()
    if ssl_ctx is None:
        logger.debug("APNs: 証明書未設定のためスキップ")
        return False

    host = _apns_host()
    url = f"{host}/3/device/{push_token}"
    payload = json.dumps({"mdm": push_magic}).encode()

    try:
        async with httpx.AsyncClient(http2=True, verify=ssl_ctx, timeout=10.0) as client:
            resp = await client.post(
                url,
                content=payload,
                headers={
                    "apns-topic": settings.apns_topic,
                    "apns-push-type": "mdm",
                    "content-type": "application/json",
                },
            )
            if resp.status_code == 200:
                logger.info(f"APNs MDM push sent | token={push_token[:8]}...")
                return True
            logger.warning(f"APNs push failed | status={resp.status_code} | {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"APNs push error | {e}")
        return False
