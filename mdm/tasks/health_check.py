"""MDMデバイス定期ヘルスチェック

毎時実行して以下を行う:
  - iOS: last_checkin_at が 24 時間以上前 → APNs 再 push（デバイスに checkin を促す）
  - iOS: profile_status が missing のデバイスを検知 → InstallProfile 再 push
  - Android: last_seen_at が 48 時間以上前 → status を inactive に更新
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import AndroidDeviceDB, CampaignDB, DeviceDB, iOSDeviceDB
from mdm.nanomdm import client as nanomdm_client
from utils import utcnow
from mdm.nanomdm import commands as mdm_commands
from mdm.nanomdm.apns import send_mdm_push

logger = logging.getLogger(__name__)

IOS_CHECKIN_THRESHOLD_HOURS = 24
ANDROID_INACTIVE_THRESHOLD_HOURS = 48


async def run_health_check(db: AsyncSession) -> dict:
    """
    定期ヘルスチェックのメインエントリーポイント。
    main.py の startup イベントから asyncio.create_task でスケジュールされる。
    """
    now = utcnow()
    results = {
        "ios_apns_pushed": 0,
        "ios_profile_restored": 0,
        "android_marked_inactive": 0,
    }

    # --- iOS: 24時間以上 checkin なし → APNs push ---
    ios_stale_threshold = now - timedelta(hours=IOS_CHECKIN_THRESHOLD_HOURS)
    stale_ios = (await db.execute(
        select(iOSDeviceDB).where(
            iOSDeviceDB.status == "active",
            iOSDeviceDB.last_checkin_at < ios_stale_threshold,
            iOSDeviceDB.push_token.isnot(None),
        )
    )).scalars().all()

    for dev in stale_ios:
        try:
            if dev.push_token and dev.push_magic and dev.topic:
                await send_mdm_push(dev.push_token, dev.push_magic, dev.topic)
                results["ios_apns_pushed"] += 1
                logger.info(f"HealthCheck: APNs re-push | udid={dev.udid[:8]}...")
        except Exception as e:
            logger.error(f"HealthCheck: APNs push failed | udid={dev.udid[:8]}... | {e}")

    # --- iOS: profile_status=missing → InstallProfile 再 push ---
    missing_ios = (await db.execute(
        select(iOSDeviceDB).where(
            iOSDeviceDB.status == "active",
            iOSDeviceDB.profile_status == "missing",
            iOSDeviceDB.push_token.isnot(None),
        )
    )).scalars().all()

    for dev in missing_ios:
        try:
            portal_device = await db.scalar(
                select(DeviceDB).where(DeviceDB.enrollment_token == dev.enrollment_token)
            )
            if not portal_device:
                continue
            campaign = await db.scalar(
                select(CampaignDB).where(CampaignDB.id == portal_device.campaign_id)
            )
            if not campaign:
                continue
            from mdm.enrollment.mobileconfig import generate_mobileconfig
            mobileconfig_data = generate_mobileconfig(campaign, dev.enrollment_token)
            install_cmd = mdm_commands.install_configuration_profile(mobileconfig_data)
            await nanomdm_client.push_command(dev.udid, install_cmd)
            if dev.push_token and dev.push_magic and dev.topic:
                await send_mdm_push(dev.push_token, dev.push_magic, dev.topic)
            dev.profile_status = "re_installing"
            results["ios_profile_restored"] += 1
            logger.info(f"HealthCheck: profile re-installing | udid={dev.udid[:8]}...")
        except Exception as e:
            logger.error(f"HealthCheck: profile restore failed | udid={dev.udid[:8]}... | {e}")

    await db.commit()

    # --- Android: 48時間以上 last_seen なし → inactive に更新 ---
    android_stale_threshold = now - timedelta(hours=ANDROID_INACTIVE_THRESHOLD_HOURS)
    stale_android = (await db.execute(
        select(AndroidDeviceDB).where(
            AndroidDeviceDB.status == "active",
            AndroidDeviceDB.last_seen_at < android_stale_threshold,
        )
    )).scalars().all()

    for dev in stale_android:
        dev.status = "inactive"
        results["android_marked_inactive"] += 1
        logger.info(f"HealthCheck: Android marked inactive | device={dev.device_id[:8]}...")

    await db.commit()

    logger.info(f"HealthCheck completed | {results}")
    return results
