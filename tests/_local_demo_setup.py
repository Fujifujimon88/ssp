"""
dsp_engine ローカル実機確認用のデモ DB セットアップ（手動実行）。

実行: cd ssp_platform && python tests/_local_demo_setup.py

本番 Postgres には一切触れない。ローカル SQLite `ssp_local.db` を作り直し、
デモ用の DSP キャンペーン・エクスチェンジ・消化/CV データを投入する。
このあと alembic stamp + uvicorn 起動はシェル側で行う。
"""
import os
import pathlib
import sys
import uuid

# プロジェクトルートを import path に追加 + import 前に環境変数を設定
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./ssp_local.db"
os.environ["APP_ENV"] = "development"

import asyncio  # noqa: E402
import random  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from auth import hash_password  # noqa: E402
from database import AsyncSessionLocal, Base, engine  # noqa: E402
from db_models import (  # noqa: E402
    DspCampaignDB, DspClickEventDB, DspConfigDB, DspConversionEventDB, DspSpendLogDB,
)

DB_FILE = "ssp_local.db"


async def main():
    pathlib.Path(DB_FILE).unlink(missing_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    rng = random.Random(42)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # ── デモ広告主キャンペーン（広告主ログイン: demo / demo1234） ──
        db.add(DspCampaignDB(
            id="demo-campaign",
            advertiser_name="デモ広告主株式会社",
            campaign_name="春の購入促進キャンペーン",
            status="active",
            daily_budget_jpy=50_000.0, total_budget_jpy=500_000.0,
            target_roas=300.0, margin_rate=0.20,
            bid_floor_jpy=200.0, bid_cap_jpy=8_000.0,
            avg_purchase_value_jpy=8_000.0, base_ctr=0.03, target_cvr=0.05,
            creative_title="春物セール 最大50%OFF",
            creative_body="今すぐチェック",
            creative_click_url="https://example.com/spring-sale",
            login_id="demo", hashed_password=hash_password("demo1234"),
        ))

        # ── SSP連携: 自社ノード + デモ外部エクスチェンジ ──
        db.add(DspConfigDB(
            name="self-ssp-node", endpoint_url="local://auction-engine/v1/bid",
            timeout_ms=80, active=True,
        ))
        db.add(DspConfigDB(
            name="demo-exchange", endpoint_url="https://demo-exchange.example.com/rtb",
            timeout_ms=200, qps_limit=500, active=True,
        ))

        # ── 消化ログ40件 + クリックイベント（直近7日・約25%クリック） ──
        # デモ表示用のため spend_jpy は読みやすいまとまった額にしている
        for _ in range(40):
            logged = now - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 23))
            cleared = round(rng.uniform(2_000, 4_000), 1)
            token = uuid.uuid4().hex
            imp_id = uuid.uuid4().hex
            platform = rng.choice(["web", "android", "ios"])
            source = rng.choice(["ssp-node", "demo-exchange"])
            db.add(DspSpendLogDB(
                campaign_id="demo-campaign",
                click_token=token,
                impression_id=imp_id,
                platform=platform,
                source=source,
                bid_price_jpy=round(cleared * 1.1, 1),
                cleared_price_jpy=cleared,
                spend_jpy=cleared,
                logged_at=logged,
            ))
            if rng.random() < 0.25:  # 約25%をクリック（配信の数分〜数十分後）
                db.add(DspClickEventDB(
                    campaign_id="demo-campaign",
                    click_token=token,
                    impression_id=imp_id,
                    platform=platform,
                    source=source,
                    clicked_at=logged + timedelta(minutes=rng.randint(1, 30)),
                ))

        # ── 購入CV 45件（直近7日） ──
        for i in range(45):
            recv = now - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 23))
            db.add(DspConversionEventDB(
                campaign_id="demo-campaign",
                click_token=uuid.uuid4().hex,
                platform=rng.choice(["web", "android", "ios"]),
                source=rng.choice(["direct", "s2s_appsflyer"]),
                event_type="purchase",
                revenue_jpy=round(rng.uniform(7_000, 9_500), 0),
                dedup_key=f"demo-cv-{i}",
                received_at=recv, attributed_at=recv,
            ))

        await db.commit()

    await engine.dispose()
    print("OK: ssp_local.db を作成しデモデータを投入しました")
    print("  - DSPキャンペーン: 春の購入促進キャンペーン（広告主ログイン demo / demo1234）")
    print("  - エクスチェンジ: self-ssp-node, demo-exchange")
    print("  - 消化ログ40件 / 購入CV45件")


if __name__ == "__main__":
    asyncio.run(main())
