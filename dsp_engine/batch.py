"""
dsp_engine サプライチェーン検証バッチ。

sellers.json / ads.txt の HTTP fetch は低遅延・高 QPS の入札パスに入れられない。
このモジュールのバックグラウンドループ（lifespan から create_task で起動）で
定期的に fetch し、結果を DB + L1 メモリキャッシュへ反映する。
"""
import asyncio
import json
import logging

from sqlalchemy import select

from config import settings
from database import AsyncSessionLocal
from db_models import PublisherDB
from dsp_engine.adstxt import fetch_ads_txt, verify_publisher_in_ads_txt
from dsp_engine.sjcache import fetch_sellers_json, prime_cache
from dsp_engine.supply import list_supply_connections
from utils import utcnow

logger = logging.getLogger(__name__)

SELLERS_JSON_REFRESH_SEC = 3600  # 1 時間ごとに sellers.json / ads.txt を再検証


async def run_sellers_json_refresh() -> None:
    """全エクスチェンジの sellers.json を再 fetch し、DB + L1 キャッシュを更新する。"""
    async with AsyncSessionLocal() as db:
        connections = await list_supply_connections(db)
        updated = 0
        for conn in connections:
            url = conn.sellers_json_url
            if not url:
                continue
            data = await fetch_sellers_json(url)
            if data is None:
                continue  # fetch 失敗はスキップ（既存キャッシュを温存）
            conn.sellers_json_cache = json.dumps(data, ensure_ascii=False)
            conn.sellers_json_cached_at = utcnow()
            prime_cache(conn.name, data)
            updated += 1
        if updated:
            await db.commit()
            logger.info(f"supply-chain batch: refreshed sellers.json for {updated} exchange(s)")


async def run_ads_txt_check() -> None:
    """全 active パブリッシャーの ads.txt に自社 SSP の DIRECT 行があるか検証する。

    検証結果はログのみ。PublisherDB のステータス自動変更等の破壊的操作はしない。
    """
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(PublisherDB).where(PublisherDB.status == "active"))
        publishers = rows.scalars().all()
        missing = 0
        for pub in publishers:
            if not pub.domain:
                continue
            entries = await fetch_ads_txt(pub.domain)
            if not verify_publisher_in_ads_txt(entries, settings.ssp_domain, pub.id):
                missing += 1
                logger.warning(
                    f"ads.txt check: SSP entry missing for publisher {pub.id} ({pub.domain})"
                )
        if missing:
            logger.info(f"supply-chain batch: ads.txt missing for {missing} publisher(s)")


async def schedule_supply_chain_tasks() -> None:
    """lifespan から create_task で起動するバックグラウンドループ。

    SELLERS_JSON_REFRESH_SEC ごとに sellers.json リフレッシュと ads.txt 検証を行う。
    例外は握りつぶしてループを継続する（バッチ失敗で本体を巻き込まない）。
    """
    while True:
        try:
            await run_sellers_json_refresh()
            await run_ads_txt_check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"supply-chain batch failed: {exc}")
        await asyncio.sleep(SELLERS_JSON_REFRESH_SEC)
