"""
dsp_engine SSP 連携（エクスチェンジ接続）管理。

既存の DspConfigDB（dsp_configs テーブル）を流用し、SSP 連携画面から
接続の登録・ステータス確認・外部IDマッピングを行う。

Phase 1 では自社 SSP ノード 1 件を既定登録して可視化する。
外部エクスチェンジの実トラフィック接続は Phase 2。
"""
import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspConfigDB

logger = logging.getLogger(__name__)

# 自社 SSP ノード（main.py の auction_engine）を表す予約済み接続名
SELF_SSP_NODE_NAME = "self-ssp-node"


async def list_supply_connections(db: AsyncSession) -> list[DspConfigDB]:
    rows = await db.execute(select(DspConfigDB).order_by(DspConfigDB.created_at))
    return list(rows.scalars().all())


async def get_supply_connection(db: AsyncSession, conn_id: str) -> Optional[DspConfigDB]:
    return await db.get(DspConfigDB, conn_id)


async def create_supply_connection(
    db: AsyncSession,
    *,
    name: str,
    endpoint_url: str,
    timeout_ms: int = 200,
    qps_limit: int = 0,
    active: bool = False,
    api_secret: Optional[str] = None,
) -> DspConfigDB:
    conn = DspConfigDB(
        name=name,
        endpoint_url=endpoint_url,
        timeout_ms=timeout_ms,
        qps_limit=qps_limit,
        active=active,
        api_secret=api_secret,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def update_supply_connection(
    db: AsyncSession, conn_id: str, **fields
) -> Optional[DspConfigDB]:
    conn = await db.get(DspConfigDB, conn_id)
    if conn is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(conn, key):
            setattr(conn, key, value)
    await db.commit()
    await db.refresh(conn)
    return conn


async def save_id_mapping(
    db: AsyncSession,
    conn_id: str,
    *,
    platform_mapping: Optional[dict] = None,
    app_mapping: Optional[dict] = None,
) -> Optional[DspConfigDB]:
    """外部サービスID → 内部エンティティのマッピングを JSON 文字列で保存する。"""
    conn = await db.get(DspConfigDB, conn_id)
    if conn is None:
        return None
    if platform_mapping is not None:
        conn.platform_mapping = json.dumps(platform_mapping, ensure_ascii=False)
    if app_mapping is not None:
        conn.app_mapping = json.dumps(app_mapping, ensure_ascii=False)
    await db.commit()
    await db.refresh(conn)
    return conn


def parse_mapping(raw: Optional[str]) -> dict:
    """platform_mapping / app_mapping の JSON 文字列を dict に戻す（不正値は {}）。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def parse_allowed_asi_domains(raw: Optional[str]) -> list[str]:
    """allowed_asi_domains の JSON 配列文字列を list[str] に戻す（不正値は []）。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(v) for v in value]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


async def ensure_self_ssp_node(db: AsyncSession) -> DspConfigDB:
    """自社 SSP ノードの接続行を冪等に用意する（SSP 連携画面の既定行）。"""
    existing = await db.scalar(
        select(DspConfigDB).where(DspConfigDB.name == SELF_SSP_NODE_NAME)
    )
    if existing:
        return existing
    conn = DspConfigDB(
        name=SELF_SSP_NODE_NAME,
        endpoint_url="local://auction-engine/v1/bid",
        timeout_ms=80,
        active=True,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    logger.info("dsp-engine: self SSP node registered in supply screen")
    return conn
