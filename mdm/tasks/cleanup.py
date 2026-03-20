"""MDMデバイス情報クリーンアップバッチ（フェーズ2用・初期は無効化）

フェーズ1: 論理削除（status=opted_out、token_revoked_at を記録）のみ
フェーズ2: このバッチを有効化して物理削除または匿名化を実行

有効化方法:
  main.py の startup イベントで以下を追加するだけ:
    asyncio.create_task(schedule_cleanup())

現在は意図的に無効化しています。
A（物理削除）または C（匿名化）への移行時に有効化してください。
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import AndroidDeviceDB, DeviceDB, iOSDeviceDB

logger = logging.getLogger(__name__)

# opted_out から何日後に処理するか
CLEANUP_AFTER_DAYS = 90


async def run_cleanup(db: AsyncSession, mode: str = "physical_delete") -> dict:
    """
    optout 後 CLEANUP_AFTER_DAYS 日以上経過したデバイス情報を処理する。

    Args:
        mode: "physical_delete"（A案）または "anonymize"（C案）
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=CLEANUP_AFTER_DAYS)
    results = {"processed": 0, "mode": mode}

    target_devices = (await db.execute(
        select(DeviceDB).where(
            DeviceDB.status == "opted_out",
            DeviceDB.token_revoked_at < threshold,
        )
    )).scalars().all()

    for device in target_devices:
        token = device.enrollment_token

        if mode == "physical_delete":
            # A案: 物理削除
            await db.execute(
                delete(AndroidDeviceDB).where(AndroidDeviceDB.enrollment_token == token)
            )
            await db.execute(
                delete(iOSDeviceDB).where(iOSDeviceDB.enrollment_token == token)
            )
            await db.delete(device)
        elif mode == "anonymize":
            # C案: 匿名化（device_id等を削除し統計データのみ残す）
            android_devs = (await db.execute(
                select(AndroidDeviceDB).where(AndroidDeviceDB.enrollment_token == token)
            )).scalars().all()
            for ad in android_devs:
                ad.device_id = f"anonymized_{ad.id[:8]}"
                ad.enrollment_token = None
                ad.fcm_token = None
                ad.gaid = None
                ad.device_fingerprint = None

            ios_devs = (await db.execute(
                select(iOSDeviceDB).where(iOSDeviceDB.enrollment_token == token)
            )).scalars().all()
            for id_ in ios_devs:
                id_.udid = f"anonymized_{id_.id[:8]}"
                id_.enrollment_token = None
                id_.push_token = None
                id_.push_magic = None

            device.enrollment_token = f"revoked_{device.id[:8]}"

        results["processed"] += 1
        logger.info(f"Cleanup [{mode}]: token={token[:8]}...")

    await db.commit()
    logger.info(f"Cleanup completed | {results}")
    return results


async def schedule_cleanup():
    """
    定期実行スケジューラー（24時間ごと）。
    フェーズ2で main.py の startup に追加して有効化する。

    NOTE: 現在は無効化されています。
    """
    import asyncio
    from database import AsyncSessionLocal

    while True:
        await asyncio.sleep(24 * 3600)  # 24時間待機
        async with AsyncSessionLocal() as db:
            try:
                await run_cleanup(db, mode="physical_delete")
            except Exception as e:
                logger.error(f"Cleanup task failed: {e}")
