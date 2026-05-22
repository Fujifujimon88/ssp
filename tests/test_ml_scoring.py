"""
#5 pCTR/pCVR/value ベースライン ML のテスト（TDD: 実装前に書く Red フェーズ）。

検証対象:
  - scoring  : shrinkage 推定（pCTR/pCVR/value を観測値と prior でブレンド）
  - segments : device(platform) セグメント別 CTR 乗数の定期バッチ算出 + L1 キャッシュ
  - bidder   : 入札時の segment 乗数反映 / campaign 別 win-rate 集計

実行: cd ssp_platform && pytest tests/test_ml_scoring.py -v
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspBidLogDB, DspCampaignDB, DspClickEventDB, DspSpendLogDB
import dsp_engine.scoring as scoring


# ── フィクスチャ ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
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
    defaults = dict(
        id="camp-1", advertiser_name="A", campaign_name="C", objective="roas",
        status="active", daily_budget_jpy=0.0, total_budget_jpy=0.0,
        target_roas=300.0, margin_rate=0.20, bid_floor_jpy=100.0, bid_cap_jpy=5000.0,
        avg_purchase_value_jpy=3000.0, base_ctr=0.01, target_cvr=0.02,
        creative_title="買ってね", creative_click_url="https://advertiser.example.com/lp",
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


def make_spend(campaign_id: str, click_token: str, platform: str = "web") -> DspSpendLogDB:
    return DspSpendLogDB(
        campaign_id=campaign_id, click_token=click_token, impression_id=None,
        platform=platform, source="ssp-node",
        bid_price_jpy=0.0, cleared_price_jpy=0.0, spend_jpy=0.0,
    )


def make_click(campaign_id: str, click_token: str, platform: str = "web") -> DspClickEventDB:
    return DspClickEventDB(
        campaign_id=campaign_id, click_token=click_token, impression_id=None,
        platform=platform, source="ssp-node",
    )


# ── scoring: shrinkage 推定 ─────────────────────────────────────

def test_predict_ctr_cold_returns_prior():
    """観測ゼロなら pCTR は campaign の base_ctr（prior）そのもの"""
    c = make_campaign(base_ctr=0.10)
    assert scoring.predict_ctr(c, {"impressions": 0, "clicks": 0}) == pytest.approx(0.10)


def test_predict_ctr_shrinkage_blends_at_threshold():
    """n=warm_threshold(50) で観測 CTR と prior が 50:50 ブレンドされる"""
    c = make_campaign(base_ctr=0.10)
    # 観測 CTR = 10/50 = 0.20 ; w = 50/(50+50) = 0.5 → 0.5*0.20 + 0.5*0.10 = 0.15
    assert scoring.predict_ctr(c, {"impressions": 50, "clicks": 10}) == pytest.approx(0.15)


def test_predict_cvr_shrinkage_blends_at_threshold():
    """pCVR は clicks をサンプル数として観測 CVR と target_cvr をブレンド"""
    c = make_campaign(target_cvr=0.10)
    # 観測 CVR = 10/50 = 0.20 ; w = 0.5 → 0.15
    assert scoring.predict_cvr(c, {"clicks": 50, "conversions": 10}) == pytest.approx(0.15)


def test_predict_value_shrinkage_blends_at_threshold():
    """value は conversions をサンプル数として観測単価と avg_purchase をブレンド"""
    c = make_campaign(avg_purchase_value_jpy=1000.0)
    # 観測単価 = 100000/50 = 2000 ; w = 0.5 → 1500
    assert scoring.predict_value(c, {"conversions": 50, "revenue_jpy": 100_000.0}) == pytest.approx(1500.0)


def test_expected_value_composition_cold():
    """EV/imp = pCTR × pCVR × value（コールド時は旧コールド式と同値）"""
    c = make_campaign(base_ctr=0.10, target_cvr=0.20, avg_purchase_value_jpy=3000.0)
    # 0.10 * 0.20 * 3000 = 60
    assert scoring.expected_value_per_impression(c, {"impressions": 0}) == pytest.approx(60.0)


def test_compute_bid_cpm_ctr_multiplier_scales():
    """ctr_multiplier は pCTR を線形にスケールする（クランプ外なら入札も比例）"""
    c = make_campaign(
        base_ctr=0.10, target_cvr=0.10, avg_purchase_value_jpy=10_000.0,
        margin_rate=0.0, bid_floor_jpy=1.0, bid_cap_jpy=1e12,
    )
    stats = {"impressions": 0, "clicks": 0, "conversions": 0, "revenue_jpy": 0.0}
    base = scoring.compute_bid_cpm_jpy(c, stats)
    doubled = scoring.compute_bid_cpm_jpy(c, stats, ctr_multiplier=2.0)
    assert doubled == pytest.approx(base * 2.0)


def test_warm_threshold_is_config_driven(monkeypatch):
    """warm_threshold を変えると shrinkage の重みが変わる"""
    from config import settings
    c = make_campaign(base_ctr=0.10)
    stats = {"impressions": 100, "clicks": 20}  # 観測 CTR 0.20
    monkeypatch.setattr(settings, "warm_threshold", 100)
    p_big = scoring.predict_ctr(c, stats)   # w = 100/200 = 0.5 → 0.15
    monkeypatch.setattr(settings, "warm_threshold", 0)
    p_zero = scoring.predict_ctr(c, stats)  # w = 1.0 → 観測値 0.20
    assert p_big == pytest.approx(0.15)
    assert p_zero == pytest.approx(0.20)


# ── segments: device セグメント乗数 ────────────────────────────

def test_platform_of():
    """OpenRTB Device から platform セグメントを導出する"""
    from auction.openrtb import Device
    from dsp_engine.segments import platform_of
    assert platform_of(Device(os="Android")) == "android"
    assert platform_of(Device(os="iOS")) == "ios"
    assert platform_of(Device(devicetype=1)) == "web"
    assert platform_of(None) == "unknown"
    assert platform_of(Device(os="Windows")) == "unknown"


def test_get_segment_multiplier_default_is_one():
    """未知セグメントの乗数は 1.0（影響なし）"""
    from dsp_engine.segments import get_segment_multiplier
    assert get_segment_multiplier("no-such-segment") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_recompute_segment_multipliers(db, monkeypatch):
    """platform 別 CTR 乗数をバッチ算出し [0.5, 2.0] でクランプ、L1 キャッシュへ反映"""
    from dsp_engine import segments
    monkeypatch.setattr(segments, "SEG_MIN_SAMPLES", 2)  # 少量シードで検証
    db.add(make_campaign(id="camp-seg"))
    await db.commit()
    # android 4imp/4click(CTR1.0) ; ios 4imp/0click(0.0) ; web 4imp/1click(0.25)
    # overall = 12imp / 5click = 0.4167
    plan = {"android": 4, "ios": 0, "web": 1}
    for plat, clicks in plan.items():
        for i in range(4):
            db.add(make_spend("camp-seg", f"sp-{plat}-{i}", platform=plat))
        for i in range(clicks):
            db.add(make_click("camp-seg", f"ck-{plat}-{i}", platform=plat))
    await db.commit()

    mults = await segments.recompute_segment_multipliers(db)
    assert mults["android"] == pytest.approx(2.0)   # 1.0/0.4167=2.4 → clamp 2.0
    assert mults["ios"] == pytest.approx(0.5)        # 0.0 → clamp 0.5
    assert mults["web"] == pytest.approx(0.6, abs=0.05)  # 0.25/0.4167≈0.6（クランプ外）
    # L1 キャッシュも更新される
    assert segments.get_segment_multiplier("android") == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_segment_multiplier_low_sample_defaults_to_one(db):
    """SEG_MIN_SAMPLES 未満のセグメントは乗数 1.0（信頼不足）"""
    from dsp_engine import segments
    db.add(make_campaign(id="camp-ls"))
    await db.commit()
    for i in range(3):  # 既定 SEG_MIN_SAMPLES(100) 未満
        db.add(make_spend("camp-ls", f"ls-{i}", platform="android"))
    db.add(make_click("camp-ls", "lsc-0", platform="android"))
    await db.commit()
    mults = await segments.recompute_segment_multipliers(db)
    assert mults.get("android", 1.0) == pytest.approx(1.0)


# ── bidder: segment 乗数の入札反映 ─────────────────────────────

@pytest.mark.asyncio
async def test_handle_bid_request_applies_segment_multiplier(db, monkeypatch):
    """入札時に device セグメント乗数が pCTR に反映される"""
    import dsp_engine.bidder as bidder_mod
    from auction.openrtb import Banner, BidRequest, Device, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-seg-bid", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=1.0, bid_cap_jpy=1e9,
    ))
    await db.commit()
    monkeypatch.setattr(
        bidder_mod, "get_segment_multiplier",
        lambda seg: 2.0 if seg == "android" else 1.0,
    )
    imp = Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)
    resp_android = await handle_bid_request(
        BidRequest(imp=[imp], device=Device(os="Android")), db)
    resp_plain = await handle_bid_request(BidRequest(imp=[imp]), db)

    assert resp_android is not None and resp_plain is not None
    p_android = resp_android.seatbid[0].bid[0].price
    p_plain = resp_plain.seatbid[0].bid[0].price
    assert p_android == pytest.approx(p_plain * 2.0, rel=1e-6)


# ── bidder: campaign 別 win-rate ───────────────────────────────

@pytest.mark.asyncio
async def test_get_campaign_win_rates(db):
    """win-rate = wins(落札数) / bids(入札数)。no_bid ログは bids に数えない"""
    from dsp_engine.bidder import get_campaign_win_rates

    db.add(make_campaign(id="camp-wr"))
    await db.commit()
    for i in range(4):
        db.add(DspBidLogDB(campaign_id="camp-wr", source="ssp-node",
                           outcome="bid", bidfloor_usd=0.0))
    db.add(DspBidLogDB(campaign_id="camp-wr", source="ssp-node",
                       outcome="no_bid", nbr=502, bidfloor_usd=0.0))
    for i in range(2):
        db.add(make_spend("camp-wr", f"wr-{i}"))
    await db.commit()

    rates = await get_campaign_win_rates(db)
    assert rates["camp-wr"]["bids"] == 4
    assert rates["camp-wr"]["wins"] == 2
    assert rates["camp-wr"]["win_rate"] == pytest.approx(0.5)
