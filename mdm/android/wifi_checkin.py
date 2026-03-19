"""Wi-Fi SSID 来店トリガー

デバイスが特定のSSIDに接続したとき、登録済みのルールに従いアクションを実行する。

action_type:
  push  → FCMプッシュ通知を送信
  line  → エル投げ経由でLINEメッセージを送信
  point → （将来実装）ポイントを付与

拡張方法:
  action_type を追加し、_execute_action() に分岐を追加するだけ。
  action_config は JSON 自由形式のため、呼び出し側の変更不要。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import AndroidDeviceDB, DeviceDB, WifiCheckinLogDB, WifiTriggerRuleDB
from mdm.android.fcm import send_notification
from mdm.line.eru_nage import send_message

logger = logging.getLogger(__name__)


async def handle_wifi_checkin(
    device_id: str,
    ssid: str,
    db: AsyncSession,
) -> dict:
    """
    デバイスからのSSID接続イベントを受け取り、ルールに基づきアクションを実行する。

    Returns:
        {"actions_fired": [...], "skipped": bool}
    """
    # AndroidDeviceDB: fcm_token
    android_device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == device_id)
    )
    if not android_device:
        logger.warning(f"wifi_checkin: unknown device_id={device_id[:8]}")
        return {"actions_fired": [], "skipped": True}

    # DeviceDB: line_user_id（enrollment_token で紐付け）
    base_device = await db.scalar(
        select(DeviceDB).where(DeviceDB.enrollment_token == android_device.enrollment_token)
    ) if android_device.enrollment_token else None

    # クールダウンチェック：同じデバイス × SSIDで最近チェックインしていないか
    rules = (await db.scalars(
        select(WifiTriggerRuleDB).where(
            and_(
                WifiTriggerRuleDB.ssid == ssid,
                WifiTriggerRuleDB.active == True,
            )
        )
    )).all()

    if not rules:
        logger.debug(f"wifi_checkin: no rules for ssid={ssid}")
        return {"actions_fired": [], "skipped": True}

    actions_fired = []
    dealer_id = rules[0].dealer_id if rules else None

    for rule in rules:
        # クールダウン：同ルールで cooldown_minutes 以内にチェックイン済みならスキップ
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=rule.cooldown_minutes)
        recent = await db.scalar(
            select(WifiCheckinLogDB).where(
                and_(
                    WifiCheckinLogDB.device_id == device_id,
                    WifiCheckinLogDB.ssid == ssid,
                    WifiCheckinLogDB.triggered_at >= cutoff,
                )
            )
        )
        if recent:
            logger.debug(f"wifi_checkin: cooldown active for device={device_id[:8]} ssid={ssid}")
            continue

        config = json.loads(rule.action_config or "{}")
        success = await _execute_action(rule.action_type, config, android_device, base_device)
        if success:
            actions_fired.append(rule.action_type)

    # ログ記録（アクションがなくてもSSID接続は記録する）
    log = WifiCheckinLogDB(
        device_id=device_id,
        ssid=ssid,
        dealer_id=dealer_id,
        actions_fired=json.dumps(actions_fired),
    )
    db.add(log)
    await db.commit()

    logger.info(f"wifi_checkin: device={device_id[:8]} ssid={ssid} actions={actions_fired}")
    return {"actions_fired": actions_fired, "skipped": False}


async def _execute_action(
    action_type: str,
    config: dict,
    android_device: AndroidDeviceDB,
    base_device,  # DeviceDB | None
) -> bool:
    """アクションを実行する。新しいaction_typeはここに追加するだけ。"""

    if action_type == "push":
        if not android_device.fcm_token:
            logger.warning("wifi_checkin: push skipped (no fcm_token)")
            return False
        return await send_notification(
            fcm_token=android_device.fcm_token,
            title=config.get("title", "ご来店ありがとうございます！"),
            body=config.get("body", "今日のお得な情報をチェック"),
        )

    if action_type == "line":
        line_user_id = getattr(base_device, "line_user_id", None)
        if not line_user_id:
            logger.warning("wifi_checkin: line skipped (no line_user_id)")
            return False
        return await send_message(
            line_user_id=line_user_id,
            message=config.get("message", "ご来店ありがとうございます！"),
        )

    if action_type == "point":
        # 将来実装：ポイントテーブルへの加算
        logger.info(f"wifi_checkin: point action (not yet implemented) config={config}")
        return False

    logger.warning(f"wifi_checkin: unknown action_type={action_type}")
    return False
