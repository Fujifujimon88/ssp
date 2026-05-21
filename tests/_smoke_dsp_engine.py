"""
dsp_engine エンドツーエンド スモークテスト（手動実行用、pytest 非収集）。

実行: cd ssp_platform && python tests/_smoke_dsp_engine.py

一時 SQLite を使い、実際の ASGI アプリ経由で
/v1/bid → 落札 → /dsp-engine/conversion → レポート/ダッシュボードまでを検証する。
"""
import os
import pathlib
import re
import sys

# プロジェクトルートを import path に追加（tests/ から直接実行するため）
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# import より前に環境変数を設定（database.py のグローバルエンジンに反映させる）
_DB = "./_smoke_dsp.db"
pathlib.Path(_DB).unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB}"
os.environ["APP_ENV"] = "development"
os.environ["ADMIN_ALLOWED_IPS"] = "127.0.0.1,testclient"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399"  # 接続不可 → インメモリにフォールバック

import asyncio  # noqa: E402

# Windows コンソール(cp932)対策: 標準出力を UTF-8 に切り替える
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from httpx import ASGITransport, AsyncClient  # noqa: E402

from urllib.parse import urlparse  # noqa: E402

from auth import hash_password  # noqa: E402
from database import AsyncSessionLocal, Base, engine  # noqa: E402
from db_models import DspCampaignDB  # noqa: E402
from dsp.mock_dsp import create_mock_dsps  # noqa: E402
from dsp_engine import supply  # noqa: E402
from dsp_engine.bidder import LocalDspEngineDSP  # noqa: E402
from main import app, auction_engine  # noqa: E402

# ASGITransport は lifespan を起動しないため、DSP 登録を手動で再現する
for _dsp in create_mock_dsps():
    auction_engine.register_dsp(_dsp.dsp_id, _dsp)
auction_engine.register_dsp(LocalDspEngineDSP.DSP_ID, LocalDspEngineDSP())

PASS, FAIL = "  OK ", "FAIL "
_failures = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"[{PASS if ok else FAIL}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        _failures.append(label)


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # オークションに確実に勝てる強いキャンペーンを seed
    async with AsyncSessionLocal() as db:
        db.add(DspCampaignDB(
            id="smoke-camp", advertiser_name="スモーク広告主", campaign_name="スモークCP",
            status="active", daily_budget_jpy=0.0, total_budget_jpy=0.0,
            target_roas=300.0, margin_rate=0.2, bid_floor_jpy=100.0, bid_cap_jpy=200_000.0,
            avg_purchase_value_jpy=50_000.0, base_ctr=0.2, target_cvr=0.2,
            creative_title="スモーク広告", creative_click_url="https://smoke.example.com/lp",
            login_id="smoke", hashed_password=hash_password("pw123456"),
        ))
        await db.commit()

    # Phase 2: 外部エクスチェンジを登録・有効化
    async with AsyncSessionLocal() as db:
        conn = await supply.create_supply_connection(
            db, name="smoke-exchange",
            endpoint_url="https://smoke-exchange.example.com/rtb", qps_limit=100,
        )
        await supply.update_supply_connection(db, conn.id, active=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testclient") as c:

        # 1. /v1/bid — dsp-engine が落札し ad markup に dsp_ct が埋まる
        r = await c.post("/v1/bid", json={
            "publisherId": "pub-smoke", "slotId": "slot-smoke", "sizes": [[300, 250]],
        })
        bids = r.json().get("bids", [])
        adm = bids[0]["ad"] if bids else ""
        m = re.search(r"dsp_ct=([0-9a-f]+)", adm)
        check("/v1/bid が落札を返す", r.status_code == 200 and bool(bids), f"cpm={bids[0]['cpm'] if bids else None}")
        check("ad markup に click_token(dsp_ct)が埋まる", bool(m))
        click_token = m.group(1) if m else None

        # 2. /dsp-engine/conversion — click_token で購入CVをアトリビューション
        if click_token:
            r = await c.post("/dsp-engine/conversion", json={
                "dsp_ct": click_token, "revenue_jpy": 12000, "event_type": "purchase",
                "dedup_key": "smoke-evt-1",
            })
            body = r.json()
            check("購入CVポストバック受信", r.status_code == 200 and body.get("created") is True)
            # 冪等性: 同じ dedup_key は二重計上しない
            r2 = await c.post("/dsp-engine/conversion", json={
                "dsp_ct": click_token, "revenue_jpy": 12000, "dedup_key": "smoke-evt-1",
            })
            check("購入CVの冪等性（重複排除）", r2.json().get("created") is False)

        # 3. レポートAPI — 消化と売上が集計に反映される
        r = await c.get("/dsp-engine/admin/report/api?dimensions=campaign")
        rows = r.json().get("rows", [])
        row = rows[0] if rows else {}
        check("レポートAPI が集計を返す", r.status_code == 200 and bool(rows),
              f"spend={row.get('spend_jpy')} revenue={row.get('revenue_jpy')} roas={row.get('roas')}")
        check("レポートに売上が反映", row.get("revenue_jpy", 0) == 12000)

        # 4. 管理画面 HTML（3画面）が表示される
        for path, label in [
            ("/dsp-engine/admin/campaigns", "キャンペーン管理画面"),
            ("/dsp-engine/admin/supply", "SSP連携画面"),
            ("/dsp-engine/admin/report", "レポート画面"),
        ]:
            r = await c.get(path)
            check(f"{label} 表示", r.status_code == 200 and "<html" in r.text.lower())

        # 5. 広告主ログイン → ダッシュボード
        r = await c.post("/dsp-engine/advertiser/login",
                          data={"login_id": "smoke", "password": "pw123456"})
        check("広告主ログイン", r.status_code in (200, 303) and "dsp_advertiser_token" in r.cookies)
        r = await c.get("/dsp-engine/advertiser/dashboard")
        check("広告主ダッシュボード表示", r.status_code == 200 and "ROAS" in r.text)
        r = await c.get("/dsp-engine/advertiser/api/stats")
        stats = r.json()
        check("広告主KPI(JSON)", r.status_code == 200 and stats.get("revenue_jpy") == 12000,
              f"roas={stats.get('roas')}% spend={stats.get('spend_jpy')}")

        # ── Phase 2: 外部エクスチェンジ受信側入札 ──
        ortb = {"id": "ortb-1",
                "imp": [{"id": "imp-x", "banner": {"w": 300, "h": 250}, "bidfloor": 0.0}]}

        r = await c.post("/dsp-engine/exchange/smoke-exchange/bid", json=ortb)
        seat = (r.json().get("seatbid") or [{}])[0] if r.status_code == 200 else {}
        ext_bid = (seat.get("bid") or [{}])[0] if seat else {}
        nurl = ext_bid.get("nurl", "")
        check("外部エクスチェンジからの入札受信", r.status_code == 200 and bool(ext_bid))
        check("入札に落札通知URL(nurl)が含まれる",
              "/dsp-engine/win" in nurl and "src=smoke-exchange" in nurl)

        r = await c.post("/dsp-engine/exchange/unknown-exch/bid", json=ortb)
        check("未登録エクスチェンジは204でノービッド", r.status_code == 204)

        # 落札通知（${AUCTION_PRICE} を実落札価格に置換してエクスチェンジが呼ぶ想定）
        if nurl:
            pu = urlparse(nurl.replace("${AUCTION_PRICE}", "20"))
            r = await c.get(f"{pu.path}?{pu.query}")
            check("落札通知(win notice)処理", r.status_code == 200 and r.json().get("status") == "ok")

        r = await c.get("/dsp-engine/admin/report/api?dimensions=source")
        sources = [row.get("source") for row in r.json().get("rows", [])]
        check("レポートに外部エクスチェンジ source が反映", "smoke-exchange" in sources,
              f"sources={sources}")

    await engine.dispose()
    pathlib.Path(_DB).unlink(missing_ok=True)

    print()
    if _failures:
        print(f"=== {len(_failures)} 件 失敗: {_failures} ===")
        raise SystemExit(1)
    print("=== スモークテスト 全項目 PASS ===")


if __name__ == "__main__":
    asyncio.run(main())
