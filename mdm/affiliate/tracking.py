"""アフィリエイトリンク生成・クリック追跡

- クリック追跡付きURLを生成する
- クリックログをDBに保存しリダイレクト
- AppsFlyer / Adjust S2Sポストバック受信
"""
import json
import logging
from urllib.parse import urlencode

import httpx

from config import settings

logger = logging.getLogger(__name__)

# AppsFlyer S2S Install Postback エンドポイント
APPSFLYER_S2S_URL = "https://s2s.appsflyer.com/api/v2/installs"
# Adjust S2S Event エンドポイント
ADJUST_S2S_URL = "https://s2s.adjust.com/event"


def build_tracked_url(campaign_id: str, enrollment_token: str, base: str) -> str:
    """
    アフィリエイトクリック追跡URL を生成する。
    ユーザーがタップ → /mdm/affiliate/click/{campaign_id} → リダイレクト → 実際の広告主URL
    base: リクエストの origin (例: "https://ssp-platform.vercel.app")
    """
    params = urlencode({"token": enrollment_token})
    return f"{base}/mdm/affiliate/click/{campaign_id}?{params}"


async def send_appsflyer_postback(
    dev_key: str,
    app_id: str,
    advertising_id: str,
    enrollment_token: str,
) -> bool:
    """
    MDMサイレントインストール確認後にAppsFlyerへS2Sポストバックを送信する。
    （Digital Turbineと同じ方式）
    """
    params = {
        "af_customer_user_id": enrollment_token,
        "advertising_id": advertising_id,
        "app_id": app_id,
        "af_events_api": "true",
        "eventName": "install",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                APPSFLYER_S2S_URL,
                params={"devkey": dev_key},
                json=params,
            )
            resp.raise_for_status()
            logger.info(f"AppsFlyer S2S postback OK | app={app_id} | token={enrollment_token[:8]}...")
            return True
    except Exception as e:
        logger.warning(f"AppsFlyer S2S error: {e}")
        return False


async def send_adjust_postback(
    app_token: str,
    event_token: str,
    advertising_id: str,
    enrollment_token: str,
) -> bool:
    """AdjustへS2Sイベントポストバックを送信する"""
    params = {
        "app_token": app_token,
        "event_token": event_token,
        "gps_adid": advertising_id,
        "s2s": "1",
        "partner_params[enrollment_token]": enrollment_token,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(ADJUST_S2S_URL, params=params)
            resp.raise_for_status()
            logger.info(f"Adjust S2S postback OK | app={app_token} | token={enrollment_token[:8]}...")
            return True
    except Exception as e:
        logger.warning(f"Adjust S2S error: {e}")
        return False
