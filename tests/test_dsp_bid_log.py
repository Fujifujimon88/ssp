"""
#4 入札ログ完全化 + 予算 TOCTOU 対策のテスト（TDD: 実装前に書く Red フェーズ）。

検証対象:
  - 入札ログ : handle_bid_request の全分岐（入札成立 / 各 no-bid 理由）が
               nbr 付きで DspBidLogDB に記録される
  - nbr 集計 : no-bid 理由コード別カウンタ（Redis / メモリフォールバック）
  - TOCTOU   : record_dsp_win で総予算超過を検知し budget_exhausted へ自動切替
  - admin    : get_bid_log_summary が直近ログ + nbr 別件数を返す

実行: cd ssp_platform && pytest tests/test_dsp_bid_log.py -v
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspBidLogDB, DspCampaignDB, DspSpendLogDB
from dsp_engine import nbr


# ── フィクスチャ ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """インメモリ SQLite（StaticPool で単一コネクション維持）"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def make_campaign(**kw) -> DspCampaignDB:
    """全フィールドを明示指定した DspCampaignDB"""
    defaults = dict(
        id="camp-1",
        advertiser_name="テスト広告主",
        campaign_name="テストキャンペーン",
        objective="roas",
        status="active",
        daily_budget_jpy=0.0,
        total_budget_jpy=0.0,
        target_roas=300.0,
        margin_rate=0.20,
        bid_floor_jpy=100.0,
        bid_cap_jpy=5000.0,
        avg_purchase_value_jpy=3000.0,
        base_ctr=0.01,
        target_cvr=0.02,
        creative_title="買ってね",
        creative_click_url="https://advertiser.example.com/lp",
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


def make_spend(campaign_id: str, spend_jpy: float, click_token: str) -> DspSpendLogDB:
    return DspSpendLogDB(
        campaign_id=campaign_id, click_token=click_token,
        impression_id=None, platform="web", source="ssp-node",
        bid_price_jpy=0.0, cleared_price_jpy=0.0, spend_jpy=spend_jpy,
    )


def _imp(bidfloor: float = 0.0):
    from auction.openrtb import Banner, Impression
    return Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=bidfloor)


# ── 入札ログ: no-bid 各理由 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_bid_log_no_impression(db):
    """imp が無い BidRequest は nbr=NO_IMPRESSION で記録される"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    resp = await handle_bid_request(BidRequest(imp=[]), db)
    assert resp is None
    log = await db.scalar(select(DspBidLogDB))
    assert log is not None
    assert log.outcome == "no_bid"
    assert log.nbr == nbr.NBR_NO_IMPRESSION


@pytest.mark.asyncio
async def test_bid_log_no_active_campaigns(db):
    """配信中キャンペーンが無ければ nbr=NO_ACTIVE_CAMPAIGNS"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    resp = await handle_bid_request(BidRequest(imp=[_imp()]), db)
    assert resp is None
    log = await db.scalar(select(DspBidLogDB))
    assert log.nbr == nbr.NBR_NO_ACTIVE_CAMPAIGNS
    assert log.candidate_count == 0


@pytest.mark.asyncio
async def test_bid_log_all_budget_paced(db):
    """候補は居るが全て予算超過なら nbr=ALL_BUDGET_PACED"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(id="camp-x", total_budget_jpy=1.0))
    db.add(make_spend("camp-x", 100.0, "tok-x"))
    await db.commit()

    resp = await handle_bid_request(BidRequest(imp=[_imp()]), db)
    assert resp is None
    log = await db.scalar(select(DspBidLogDB))
    assert log.nbr == nbr.NBR_ALL_BUDGET_PACED
    assert log.candidate_count == 1
    assert log.paced_out_count == 1


@pytest.mark.asyncio
async def test_bid_log_below_floor(db):
    """最高入札がフロア未達なら nbr=BELOW_FLOOR"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-low", base_ctr=0.001, target_cvr=0.001,
        avg_purchase_value_jpy=100.0, bid_floor_jpy=1.0, bid_cap_jpy=100.0,
    ))
    await db.commit()

    resp = await handle_bid_request(BidRequest(imp=[_imp(bidfloor=9999.0)]), db)
    assert resp is None
    log = await db.scalar(select(DspBidLogDB))
    assert log.nbr == nbr.NBR_BELOW_FLOOR
    assert log.candidate_count == 1


@pytest.mark.asyncio
async def test_bid_log_shaded_below_floor(db, monkeypatch):
    """bid shading 後にフロア未達なら nbr=SHADED_BELOW_FLOOR（shading をモックして発火させる）"""
    from auction.openrtb import BidRequest
    import dsp_engine.bidder as bidder_mod
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-s", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    await db.commit()
    # shading が必ずフロア未満（0円）を返すよう差し替え
    monkeypatch.setattr(bidder_mod, "compute_shaded_bid", lambda *a, **k: 0.0)

    resp = await handle_bid_request(BidRequest(imp=[_imp(bidfloor=0.01)], at=1), db)
    assert resp is None
    log = await db.scalar(select(DspBidLogDB))
    assert log.nbr == nbr.NBR_SHADED_BELOW_FLOOR
    assert log.shaded is True


# ── 入札ログ: 入札成立 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bid_log_successful_bid(db):
    """入札成立時は outcome=bid / nbr=None / campaign_id・価格を記録"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-win", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    await db.commit()

    resp = await handle_bid_request(BidRequest(imp=[_imp()]), db)
    assert resp is not None
    log = await db.scalar(select(DspBidLogDB))
    assert log.outcome == "bid"
    assert log.nbr is None
    assert log.campaign_id == "camp-win"
    assert log.bid_price_usd > 0
    assert log.bid_cpm_jpy > 0


@pytest.mark.asyncio
async def test_bid_log_candidate_and_paced_counts(db):
    """candidate_count / paced_out_count が候補数・除外数を正しく反映する"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-ok", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    db.add(make_campaign(
        id="camp-paced", total_budget_jpy=1.0, base_ctr=0.1, target_cvr=0.1,
        avg_purchase_value_jpy=10_000.0, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    db.add(make_spend("camp-paced", 100.0, "tok-paced"))
    await db.commit()

    resp = await handle_bid_request(BidRequest(imp=[_imp()]), db)
    assert resp is not None
    log = await db.scalar(select(DspBidLogDB))
    assert log.candidate_count == 2
    assert log.paced_out_count == 1
    assert log.campaign_id == "camp-ok"


# ── nbr 集計カウンタ ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_nbr_counter_incremented(db):
    """no-bid 時に nbr 別カウンタが加算される"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import get_nbr_counts, handle_bid_request

    before = (await get_nbr_counts()).get(str(nbr.NBR_NO_ACTIVE_CAMPAIGNS), 0)
    await handle_bid_request(BidRequest(imp=[_imp()]), db)
    after = (await get_nbr_counts()).get(str(nbr.NBR_NO_ACTIVE_CAMPAIGNS), 0)
    assert after == before + 1


# ── TOCTOU: 総予算超過の自動 budget_exhausted ──────────────────

@pytest.mark.asyncio
async def test_record_dsp_win_exhausts_total_budget(db):
    """record_dsp_win で累計消化が total_budget を超えたら budget_exhausted へ"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-exh", total_budget_jpy=5.0))
    await db.commit()

    # cleared 30 USD CPM → 30×150=4500円CPM → 1imp 消化 = 4.5円
    await record_dsp_win(
        db, campaign_id="camp-exh", click_token="ct-e1", impression_id="i1",
        cleared_price_usd=30.0, bid_price_usd=33.0,
    )
    camp = await db.get(DspCampaignDB, "camp-exh")
    assert camp.status == "active"  # 4.5 < 5.0

    await record_dsp_win(
        db, campaign_id="camp-exh", click_token="ct-e2", impression_id="i2",
        cleared_price_usd=30.0, bid_price_usd=33.0,
    )
    await db.refresh(camp)
    assert camp.status == "budget_exhausted"  # 9.0 >= 5.0


@pytest.mark.asyncio
async def test_exhausted_campaign_stops_bidding(db):
    """budget_exhausted になったキャンペーンは以降ノービッド"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import handle_bid_request, record_dsp_win

    db.add(make_campaign(
        id="camp-stop", total_budget_jpy=1.0, base_ctr=0.1, target_cvr=0.1,
        avg_purchase_value_jpy=10_000.0, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    await db.commit()

    await record_dsp_win(
        db, campaign_id="camp-stop", click_token="ct-s1", impression_id="i1",
        cleared_price_usd=30.0, bid_price_usd=33.0,
    )
    camp = await db.get(DspCampaignDB, "camp-stop")
    assert camp.status == "budget_exhausted"

    resp = await handle_bid_request(BidRequest(imp=[_imp()]), db)
    assert resp is None


# ── pacing: record_spend の累計返却 ────────────────────────────

@pytest.mark.asyncio
async def test_record_spend_returns_cumulative():
    """record_spend は加算後の累計額を返す"""
    from dsp_engine.pacing import BudgetPacer

    pacer = BudgetPacer()
    cid = "pace-" + uuid.uuid4().hex
    assert await pacer.record_spend(cid, 10.0) == pytest.approx(10.0)
    assert await pacer.record_spend(cid, 5.0) == pytest.approx(15.0)
    assert await pacer.get_spend(cid) == pytest.approx(15.0)


# ── admin: 入札ログサマリー ────────────────────────────────────

@pytest.mark.asyncio
async def test_bid_log_summary(db):
    """get_bid_log_summary が直近ログ + nbr 別件数を返す"""
    from auction.openrtb import BidRequest
    from dsp_engine.bidder import get_bid_log_summary, handle_bid_request

    # 1 件目: 候補なし → no-bid(500)
    await handle_bid_request(BidRequest(imp=[_imp()]), db)
    # 2 件目: キャンペーン追加して入札成立
    db.add(make_campaign(
        id="camp-sm", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    await db.commit()
    await handle_bid_request(BidRequest(imp=[_imp()]), db)

    summary = await get_bid_log_summary(db, limit=10)
    assert len(summary["recent"]) == 2
    assert summary["nbr_breakdown"].get("bid") == 1
    assert summary["nbr_breakdown"].get(str(nbr.NBR_NO_ACTIVE_CAMPAIGNS)) == 1
