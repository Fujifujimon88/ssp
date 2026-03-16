"""エル投げ API クライアント

外部APIエンドポイント（実装済み）:
  GET  /api/external/users              ← テナント内のLINEユーザー一覧
  POST /api/external/send-message       ← LINEメッセージ送信

認証: x-api-key ヘッダー

重要:
  ユーザー登録はエル投げ側のLINE Webhook（友だち追加イベント）で自動処理される。
  外部APIからユーザーを新規登録するエンドポイントは存在しない。
  エンロール完了後はメッセージ送信のみ対応。
"""
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

_SEND_MESSAGE_PATH = "/api/external/send-message"
_USERS_PATH = "/api/external/users"


def _headers() -> dict:
    return {
        "x-api-key": settings.eru_nage_api_key,
        "Content-Type": "application/json",
    }


async def send_message(
    line_user_id: str,
    message: str,
    extra_messages: Optional[list[dict]] = None,
) -> bool:
    """
    エル投げ経由でLINEメッセージを送信する。

    Args:
        line_user_id: LINE User ID（Uで始まる33文字）
        message:      テキストメッセージ（1件目）
        extra_messages: 追加メッセージオブジェクト（最大4件追加、合計5件まで）
                        例: [{"type": "image", "originalContentUrl": "...", "previewImageUrl": "..."}]

    Returns:
        True: 送信成功 / False: スキップ or エラー
    """
    if not settings.eru_nage_api_key or not line_user_id:
        logger.debug("eru_nage: skipped (no api_key or line_user_id)")
        return False

    messages = [{"type": "text", "text": message}]
    if extra_messages:
        messages.extend(extra_messages[:4])  # 合計5件まで

    payload = {
        "to": line_user_id,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.eru_nage_api_url}{_SEND_MESSAGE_PATH}",
                json=payload,
                headers=_headers(),
            )
            if resp.status_code == 404 and "User not found" in resp.text:
                logger.warning(f"eru_nage: user not in tenant | line_user_id={line_user_id[:8]}...")
                return False
            resp.raise_for_status()
            logger.info(f"eru_nage: message sent | line_user_id={line_user_id[:8]}...")
            return True
    except httpx.HTTPStatusError as e:
        logger.warning(f"eru_nage: HTTP {e.response.status_code} | {e.response.text[:200]}")
    except Exception as e:
        logger.warning(f"eru_nage: error | {e}")

    return False


async def send_enrollment_complete(
    line_user_id: str,
    platform: str = "unknown",
    age_group: Optional[str] = None,
) -> bool:
    """
    エンロール完了時にウェルカムメッセージを送信する。

    ユーザーはLINE友だち追加 → エル投げのWebhookで自動登録済みのはず。
    このメソッドでメッセージを送ることでステップ配信を補完する。
    """
    platform_name = {"ios": "iPhone", "android": "Android"}.get(platform, "スマートフォン")
    text = (
        f"✅ セットアップ完了！\n\n"
        f"{platform_name}へのサービス設定が完了しました。\n\n"
        f"🎁 今後、お得なアプリやサービスのご案内をお届けします。\n"
        f"お楽しみに！"
    )
    return await send_message(line_user_id, text)


async def get_users() -> list[dict]:
    """
    テナントに登録済みのLINEユーザー一覧を取得する。
    管理画面での確認用。

    Returns:
        ユーザーのリスト（エラー時は空リスト）
    """
    if not settings.eru_nage_api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.eru_nage_api_url}{_USERS_PATH}",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("users", [])
    except Exception as e:
        logger.warning(f"eru_nage: get_users error | {e}")
        return []


# 後方互換性のために残す（LINE Webhook経由で登録されるため実際は不要）
async def register_user(
    line_user_id: str,
    scenario_id: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> bool:
    """
    エンロール完了後にウェルカムメッセージを送信する。

    Note: エル投げへのユーザー登録はLINE友だち追加Webhookで自動処理される。
          このメソッドはエンロール完了の通知メッセージを送るのみ。
    """
    platform = (attributes or {}).get("platform", "unknown")
    age_group = (attributes or {}).get("age_group")
    return await send_enrollment_complete(line_user_id, platform, age_group)
