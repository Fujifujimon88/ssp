"""Android MDM コマンドキュー管理

DPC APKが定期ポーリングして実行するコマンドを管理する。
コマンド種別:
  install_apk       - APKサイレントインストール（CPI案件）
  add_webclip       - ホーム画面にWebクリップ追加
  show_notification - FCMプッシュ通知（エル投げ連携）
  update_lockscreen - ロック画面広告コンテンツ更新
  remove_app        - アプリアンインストール
  remove_mdm_profile - MDM管理権限の自己解除（optout）
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import AndroidCommandDB, AndroidDeviceDB

logger = logging.getLogger(__name__)


async def enqueue_command(
    db: AsyncSession,
    device_id: str,
    command_type: str,
    payload: dict,
    campaign_id: Optional[str] = None,
    store_id: Optional[str] = None,
) -> AndroidCommandDB:
    """
    デバイスへのコマンドをキューに追加する。

    Args:
        db: DBセッション
        device_id: Android ID
        command_type: コマンド種別
        payload: コマンドパラメータ（dict）
        campaign_id: アフィリエイトキャンペーンID（サーバー主権で保存）
        store_id: 店舗ID（代理店内での店舗識別）

    Returns:
        作成されたコマンドレコード
    """
    cmd = AndroidCommandDB(
        device_id=device_id,
        command_type=command_type,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        campaign_id=campaign_id,
        store_id=store_id,
    )
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    logger.info(f"Command enqueued | device={device_id[:8]}... | type={command_type} | id={cmd.id}")
    return cmd


async def get_pending_commands(db: AsyncSession, device_id: str) -> list[AndroidCommandDB]:
    """pending状態のコマンドを取得し、sent に更新して返す"""
    result = await db.execute(
        select(AndroidCommandDB)
        .where(
            AndroidCommandDB.device_id == device_id,
            AndroidCommandDB.status == "pending",
        )
        .order_by(AndroidCommandDB.created_at)
    )
    commands = list(result.scalars().all())

    if commands:
        now = datetime.now(timezone.utc)
        ids = [cmd.id for cmd in commands]
        await db.execute(
            update(AndroidCommandDB)
            .where(AndroidCommandDB.id.in_(ids))
            .values(status="sent", sent_at=now)
        )
        await db.commit()
        for cmd in commands:
            cmd.status = "sent"
            cmd.sent_at = now

    return commands


async def acknowledge_command(db: AsyncSession, command_id: str, success: bool = True) -> bool:
    """DPCからのACKを受けてコマンドのステータスを更新する"""
    result = await db.execute(
        select(AndroidCommandDB).where(AndroidCommandDB.id == command_id)
    )
    cmd = result.scalar_one_or_none()
    if not cmd:
        return False

    cmd.status = "acknowledged" if success else "failed"
    cmd.acked_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(f"Command acked | id={command_id} | success={success}")
    return True


async def update_device_last_seen(db: AsyncSession, device_id: str) -> None:
    """デバイスのlast_seen_atを更新する"""
    result = await db.execute(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if device:
        device.last_seen_at = datetime.now(timezone.utc)
        await db.commit()


async def enqueue_remove_mdm_profile(db: AsyncSession, device_id: str) -> AndroidCommandDB:
    """
    MDM管理権限の自己解除コマンドをキューに追加する（optout用）。
    DPCが次回ポーリング時に受け取り、Device Admin権限を自己解除する。
    """
    return await enqueue_command(
        db=db,
        device_id=device_id,
        command_type="remove_mdm_profile",
        payload={"reason": "user_optout"},
    )
