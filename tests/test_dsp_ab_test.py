"""
dsp_engine 優先タスク #7「A/B テスト・holdout 基盤」のユニットテスト。

TDD Red フェーズ: 実装前に先に書く失敗テスト。検証対象:
  - DspCreativeDB        : クリエイティブ 1:N 化（campaign : creatives = 1:N）
  - select_creative      : weight 比例の決定的クリエイティブ選択（純粋関数）
  - is_holdout           : holdout バケット判定（純粋関数・決定的）
  - handle_bid_request   : bid.crid = 実クリエイティブID 是正 / click_token を bid.ext で運搬
  - record_dsp_win       : 落札時に選択クリエイティブの creative_id を記録
  - run_ab_experiment_report : クリエイティブ別実績 + holdout 件数のレポート
  - DspAbExperimentDB    : 実験の作成・winner 宣言（concluded）

実行: cd ssp_platform && pytest tests/test_dsp_ab_test.py -v
"""
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import (
    DspAbExperimentDB,
    DspBidLogDB,
    DspCampaignDB,
    DspCreativeDB,
    DspSpendLogDB,
)


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
    """全フィールドを明示指定した DspCampaignDB（ORM default は flush 時のみ適用のため）"""
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
        bid_cap_jpy=100_000.0,
        avg_purchase_value_jpy=10_000.0,
        base_ctr=0.1,
        target_cvr=0.1,
        creative_id="camp-1-cr",
        creative_title="買ってね",
        creative_click_url="https://advertiser.example.com/lp",
        holdout_rate=0.0,
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


def make_creative(**kw) -> DspCreativeDB:
    defaults = dict(
        campaign_id="camp-1",
        name="素材",
        title="タイトル",
        click_url="https://advertiser.example.com/lp",
        status="active",
        weight=100,
    )
    defaults.update(kw)
    return DspCreativeDB(**defaults)


# ── クリエイティブ 1:N 化 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_can_have_multiple_creatives(db):
    """1キャンペーンに複数クリエイティブを登録でき、一覧取得できる"""
    from dsp_engine.campaign_manager import create_creative, list_creatives

    db.add(make_campaign(id="camp-multi"))
    await db.commit()

    await create_creative(db, campaign_id="camp-multi", name="A", title="A 訴求")
    await create_creative(db, campaign_id="camp-multi", name="B", title="B 訴求")

    creatives = await list_creatives(db, "camp-multi")
    assert len(creatives) == 2
    assert {c.name for c in creatives} == {"A", "B"}


@pytest.mark.asyncio
async def test_create_creative_defaults(db):
    """クリエイティブ作成時、status=active / weight>0 が既定で入る"""
    from dsp_engine.campaign_manager import create_creative

    db.add(make_campaign(id="camp-def"))
    await db.commit()

    cr = await create_creative(db, campaign_id="camp-def", name="既定", title="t")
    assert cr.id
    assert cr.status == "active"
    assert cr.weight > 0


# ── select_creative（weight 比例の決定的選択・純粋関数） ──────

def test_select_creative_deterministic():
    """同一 seed では常に同じクリエイティブが選ばれる（決定的）"""
    from dsp_engine.bidder import select_creative

    creatives = [
        make_creative(id="cr-a", weight=100),
        make_creative(id="cr-b", weight=100),
    ]
    first = select_creative(creatives, seed="req-xyz")
    for _ in range(20):
        assert select_creative(creatives, seed="req-xyz").id == first.id


def test_select_creative_weight_distribution():
    """weight 90:10 では重い方が明確に多く選ばれる"""
    from dsp_engine.bidder import select_creative

    creatives = [
        make_creative(id="cr-heavy", weight=90),
        make_creative(id="cr-light", weight=10),
    ]
    picks = [select_creative(creatives, seed=f"req-{i}").id for i in range(400)]
    heavy = picks.count("cr-heavy")
    assert 280 < heavy < 380  # 期待 360 付近。ハッシュ分散の許容幅


def test_select_creative_single_always_selected():
    """クリエイティブが1件ならそれが常に選ばれる（後方互換の基礎）"""
    from dsp_engine.bidder import select_creative

    creatives = [make_creative(id="cr-only", weight=50)]
    assert select_creative(creatives, seed="any").id == "cr-only"


def test_select_creative_skips_paused():
    """status=paused のクリエイティブは weight が大きくても選ばれない"""
    from dsp_engine.bidder import select_creative

    creatives = [
        make_creative(id="cr-paused", weight=10_000, status="paused"),
        make_creative(id="cr-active", weight=1, status="active"),
    ]
    for i in range(50):
        assert select_creative(creatives, seed=f"s-{i}").id == "cr-active"


def test_select_creative_empty_returns_none():
    """選択可能なクリエイティブが無ければ None（呼び出し側がフォールバック）"""
    from dsp_engine.bidder import select_creative

    assert select_creative([], seed="x") is None
    assert select_creative([make_creative(status="paused")], seed="x") is None


# ── is_holdout（holdout バケット判定・純粋関数） ──────────────

def test_is_holdout_zero_never():
    """holdout_rate=0.0 は常に False（全件入札）"""
    from dsp_engine.bidder import is_holdout

    assert all(not is_holdout(0.0, seed=f"s-{i}") for i in range(100))


def test_is_holdout_one_always():
    """holdout_rate=1.0 は常に True（全件 holdout）"""
    from dsp_engine.bidder import is_holdout

    assert all(is_holdout(1.0, seed=f"s-{i}") for i in range(100))


def test_is_holdout_deterministic():
    """同一 seed では holdout 判定が安定する"""
    from dsp_engine.bidder import is_holdout

    first = is_holdout(0.5, seed="seed-fixed")
    for _ in range(20):
        assert is_holdout(0.5, seed="seed-fixed") is first


# ── handle_bid_request（crid 是正 / ext で click_token 運搬） ─

@pytest.mark.asyncio
async def test_handle_bid_request_crid_is_creative_id(db):
    """入札の bid.crid は実クリエイティブID（click_token の流用ではない）"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(id="camp-crid"))
    db.add(make_creative(id="cr-1", campaign_id="camp-crid", weight=100))
    db.add(make_creative(id="cr-2", campaign_id="camp-crid", weight=100))
    await db.commit()

    req = BidRequest(
        id="req-crid",
        imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)],
    )
    resp = await handle_bid_request(req, db)

    assert resp is not None
    bid = resp.seatbid[0].bid[0]
    assert bid.crid in {"cr-1", "cr-2"}  # 実クリエイティブID


@pytest.mark.asyncio
async def test_handle_bid_request_ext_carries_click_token(db):
    """click_token は bid.ext.dsp_click_token で運ばれ、ad markup にも埋まる"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(id="camp-ext"))
    db.add(make_creative(id="cr-ext", campaign_id="camp-ext"))
    await db.commit()

    req = BidRequest(
        id="req-ext",
        imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)],
    )
    resp = await handle_bid_request(req, db)
    bid = resp.seatbid[0].bid[0]

    assert bid.ext is not None
    token = bid.ext.get("dsp_click_token")
    assert token and token != bid.crid          # crid とは別物
    assert f"ct={token}" in bid.adm              # クリック計測は引き続き機能


@pytest.mark.asyncio
async def test_handle_bid_request_holdout_suppresses_bid(db):
    """holdout_rate=1.0 のキャンペーンはノービッドし、bid log に NBR_HOLDOUT を残す"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request
    from dsp_engine.nbr import NBR_HOLDOUT

    db.add(make_campaign(id="camp-hold", holdout_rate=1.0))
    db.add(make_creative(id="cr-hold", campaign_id="camp-hold"))
    await db.commit()

    req = BidRequest(
        id="req-hold",
        imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)],
    )
    assert await handle_bid_request(req, db) is None

    logs = (await db.scalars(
        select(DspBidLogDB).where(DspBidLogDB.nbr == NBR_HOLDOUT)
    )).all()
    assert len(logs) == 1
    assert logs[0].campaign_id == "camp-hold"


# ── record_dsp_win（選択クリエイティブの creative_id を記録） ─

@pytest.mark.asyncio
async def test_record_dsp_win_uses_passed_creative_id(db):
    """record_dsp_win に渡した creative_id が spend log に記録される"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-win", creative_id="camp-win-cr"))
    await db.commit()

    log = await record_dsp_win(
        db, campaign_id="camp-win", click_token="ct-win", impression_id=None,
        cleared_price_usd=10.0, bid_price_usd=11.0, creative_id="cr-selected",
    )
    assert log.creative_id == "cr-selected"


@pytest.mark.asyncio
async def test_record_dsp_win_falls_back_to_campaign_creative(db):
    """creative_id 未指定なら campaign.creative_id にフォールバック（後方互換）"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-fb", creative_id="camp-fb-cr"))
    await db.commit()

    log = await record_dsp_win(
        db, campaign_id="camp-fb", click_token="ct-fb", impression_id=None,
        cleared_price_usd=10.0, bid_price_usd=11.0,
    )
    assert log.creative_id == "camp-fb-cr"


# ── run_ab_experiment_report（クリエイティブ別 + holdout 件数） ─

@pytest.mark.asyncio
async def test_run_ab_experiment_report(db):
    """クリエイティブ別の実績と holdout 件数を A/B 実験レポートで返す"""
    from dsp_engine.nbr import NBR_HOLDOUT
    from dsp_engine.reporting import run_ab_experiment_report

    db.add(make_campaign(id="camp-rep"))
    db.add(make_creative(id="cr-A", campaign_id="camp-rep", name="A"))
    db.add(make_creative(id="cr-B", campaign_id="camp-rep", name="B"))
    # クリエイティブ別 spend ログ（A=2件 / B=1件）
    for ct in ("a1", "a2"):
        db.add(DspSpendLogDB(campaign_id="camp-rep", click_token=ct,
                             creative_id="cr-A", spend_jpy=5.0))
    db.add(DspSpendLogDB(campaign_id="camp-rep", click_token="b1",
                         creative_id="cr-B", spend_jpy=3.0))
    # holdout の bid log 1件
    db.add(DspBidLogDB(outcome="no_bid", nbr=NBR_HOLDOUT, campaign_id="camp-rep"))
    await db.commit()

    today = date.today()
    report = await run_ab_experiment_report(
        db, "camp-rep", date_from=today - timedelta(days=1), date_to=today + timedelta(days=1)
    )

    by_creative = {r["creative"]: r for r in report["creatives"]}
    assert by_creative["cr-A"]["impressions"] == 2
    assert by_creative["cr-B"]["impressions"] == 1
    assert report["holdout_requests"] == 1


# ── DspAbExperimentDB（実験の作成・winner 宣言） ──────────────

@pytest.mark.asyncio
async def test_experiment_create_and_conclude(db):
    """A/B 実験を作成し、winner を宣言して concluded にできる"""
    from dsp_engine.campaign_manager import (
        conclude_experiment, create_experiment, list_experiments,
    )

    db.add(make_campaign(id="camp-exp"))
    db.add(make_creative(id="cr-x", campaign_id="camp-exp"))
    await db.commit()

    exp = await create_experiment(db, campaign_id="camp-exp", name="訴求A/B")
    assert exp.status == "active"
    assert exp.winner_creative_id is None

    concluded = await conclude_experiment(db, exp.id, winner_creative_id="cr-x")
    assert concluded.status == "concluded"
    assert concluded.winner_creative_id == "cr-x"
    assert concluded.concluded_at is not None

    experiments = await list_experiments(db, "camp-exp")
    assert len(experiments) == 1
