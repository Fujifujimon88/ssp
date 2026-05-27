"""
dsp_engine モジュールのユニットテスト（TDD: 実装前に先に書く Red フェーズ）

検証対象:
  - scoring  : 入札価格 CPM(円) の算出（コールドスタート式 / 実績式 / フロア・キャップ）
  - pacing   : 予算ペーシングの境界（無制限 / ペース内 / ペース超過）
  - attribution : 購入CVの冪等性 / click_token アトリビューション / ROAS 集計

実行: cd ssp_platform && pytest tests/test_dsp_engine.py -v
"""
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspCampaignDB, DspConversionEventDB, DspSpendLogDB

from dsp_engine.scoring import compute_bid_cpm_jpy, expected_value_per_impression
from dsp_engine.pacing import BudgetPacer, paced_budget_allowed
from dsp_engine.attribution import get_campaign_roas, record_conversion


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
        bid_cap_jpy=5000.0,
        avg_purchase_value_jpy=3000.0,
        base_ctr=0.01,
        target_cvr=0.02,
        creative_title="買ってね",
        creative_click_url="https://advertiser.example.com/lp",
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


# ── scoring ────────────────────────────────────────────────────

def test_scoring_cold_start_formula():
    """コールドスタート: bid = base_ctr × target_cvr × avg_purchase × (1-margin) × 1000"""
    c = make_campaign(
        base_ctr=0.02, target_cvr=0.05, avg_purchase_value_jpy=2000.0,
        margin_rate=0.20, bid_floor_jpy=1.0, bid_cap_jpy=1_000_000.0,
    )
    stats = {"impressions": 0, "conversions": 0, "revenue_jpy": 0.0}
    # ev = 0.02 * 0.05 * 2000 = 2.0 ; cpm = 2.0 * 0.8 * 1000 = 1600
    assert abs(expected_value_per_impression(c, stats) - 2.0) < 1e-9
    assert abs(compute_bid_cpm_jpy(c, stats) - 1600.0) < 1e-6


def test_scoring_warm_blends_observed_with_prior():
    """実績が貯まると bid は観測実績へ shrinkage でブレンドされる（#5: 旧 cliff を廃止）"""
    c = make_campaign(
        margin_rate=0.0, bid_floor_jpy=1.0, bid_cap_jpy=1e12,
        base_ctr=0.01, target_cvr=0.02, avg_purchase_value_jpy=3000.0,
    )
    cold = {"impressions": 0, "clicks": 0, "conversions": 0, "revenue_jpy": 0.0}
    # 観測 CTR/CVR/単価すべて prior を上回る高パフォーマンス実績
    warm = {"impressions": 1000, "clicks": 100, "conversions": 30, "revenue_jpy": 300_000.0}
    cold_cpm = compute_bid_cpm_jpy(c, cold)
    warm_cpm = compute_bid_cpm_jpy(c, warm)
    # 観測実績が prior より高い → warm の入札はコールドを上回る
    assert warm_cpm > cold_cpm
    # かつ「観測値そのまま」よりは低い（shrinkage が prior へ引き戻す）
    pure_observed_cpm = (100 / 1000) * (30 / 100) * (300_000 / 30) * 1000.0
    assert warm_cpm < pure_observed_cpm


def test_scoring_floor_clamp():
    """算出値がフロア未満ならフロアにクランプ"""
    c = make_campaign(base_ctr=0.0, target_cvr=0.0, bid_floor_jpy=250.0, bid_cap_jpy=5000.0)
    assert compute_bid_cpm_jpy(c, {"impressions": 0, "conversions": 0, "revenue_jpy": 0.0}) == 250.0


def test_scoring_cap_clamp():
    """算出値がキャップ超過ならキャップにクランプ"""
    c = make_campaign(
        base_ctr=0.9, target_cvr=0.9, avg_purchase_value_jpy=100_000.0,
        margin_rate=0.0, bid_floor_jpy=1.0, bid_cap_jpy=3000.0,
    )
    assert compute_bid_cpm_jpy(c, {"impressions": 0, "conversions": 0, "revenue_jpy": 0.0}) == 3000.0


# ── pacing ─────────────────────────────────────────────────────

def test_paced_budget_allowed_unlimited():
    """日予算 0 = 無制限 → inf"""
    assert paced_budget_allowed(0.0, datetime(2026, 5, 21, 12, 0, 0)) == float("inf")


def test_paced_budget_allowed_linear():
    """12:00 時点では日予算の 12/24 が消化許容ライン"""
    # daily 24000 → hourly 1000 ; 12:00 → 1000 * 12.0 = 12000
    assert abs(paced_budget_allowed(24000.0, datetime(2026, 5, 21, 12, 0, 0)) - 12000.0) < 1e-6


@pytest.mark.asyncio
async def test_can_bid_allows_when_under_pace():
    """消化ゼロならペース内 → 入札可"""
    pacer = BudgetPacer()
    c = make_campaign(id="camp-under", daily_budget_jpy=24000.0)
    assert await pacer.can_bid(c, now=datetime(2026, 5, 21, 12, 0, 0)) is True


@pytest.mark.asyncio
async def test_can_bid_blocks_when_over_pace():
    """ペース許容(12000)×安全率(0.9)=10800 を超える消化 → 入札不可"""
    pacer = BudgetPacer()
    now = datetime(2026, 5, 21, 12, 0, 0)
    c = make_campaign(id="camp-over", daily_budget_jpy=24000.0)
    await pacer.record_spend("camp-over", 11000.0, now=now)
    assert await pacer.can_bid(c, now=now) is False


@pytest.mark.asyncio
async def test_can_bid_unlimited_budget_always_true():
    """日予算 0 のキャンペーンは常に入札可"""
    pacer = BudgetPacer()
    c = make_campaign(id="camp-unl", daily_budget_jpy=0.0)
    await pacer.record_spend("camp-unl", 9_999_999.0, now=datetime(2026, 5, 21, 3, 0, 0))
    assert await pacer.can_bid(c, now=datetime(2026, 5, 21, 3, 0, 0)) is True


# ── attribution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_conversion_is_idempotent_by_dedup_key(db):
    """同じ dedup_key の購入CVを2回受けても1行だけ記録される"""
    db.add(make_campaign(id="camp-idem"))
    await db.commit()

    _, created1 = await record_conversion(
        db, campaign_id="camp-idem", revenue_jpy=1000.0, dedup_key="evt-1"
    )
    _, created2 = await record_conversion(
        db, campaign_id="camp-idem", revenue_jpy=1000.0, dedup_key="evt-1"
    )
    assert created1 is True
    assert created2 is False

    count = await db.scalar(
        select(func.count()).select_from(DspConversionEventDB)
        .where(DspConversionEventDB.campaign_id == "camp-idem")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_conversion_attributes_via_click_token(db):
    """click_token から campaign_id / impression_id を解決して紐付ける"""
    db.add(make_campaign(id="camp-ct"))
    await db.commit()
    db.add(DspSpendLogDB(
        id="spend-ct", campaign_id="camp-ct", click_token="ct-token-1",
        impression_id="imp-99", cleared_price_jpy=200.0, spend_jpy=0.2,
    ))
    await db.commit()

    event, created = await record_conversion(db, click_token="ct-token-1", revenue_jpy=5000.0)
    assert created is True
    assert event.campaign_id == "camp-ct"
    assert event.impression_id == "imp-99"


@pytest.mark.asyncio
async def test_get_campaign_roas(db):
    """ROAS(%) = 売上合計 / 消化合計 × 100"""
    db.add(make_campaign(id="camp-roas"))
    await db.commit()
    db.add_all([
        DspSpendLogDB(id="sp1", campaign_id="camp-roas", click_token="rc1",
                      spend_jpy=300.0, cleared_price_jpy=300.0),
        DspSpendLogDB(id="sp2", campaign_id="camp-roas", click_token="rc2",
                      spend_jpy=200.0, cleared_price_jpy=200.0),
    ])
    await db.commit()
    await record_conversion(db, campaign_id="camp-roas", revenue_jpy=2000.0, dedup_key="rconv1")

    roas = await get_campaign_roas(db, "camp-roas")
    assert abs(roas["spend_jpy"] - 500.0) < 1e-6
    assert abs(roas["revenue_jpy"] - 2000.0) < 1e-6
    assert abs(roas["roas"] - 400.0) < 1e-6   # 2000 / 500 * 100
    assert roas["conversions"] == 1
    assert roas["impressions"] == 2


# ── bidder（統合: 入札 → 落札 → 計測ループ） ───────────────────

@pytest.mark.asyncio
async def test_handle_bid_request_returns_bid(db):
    """アクティブキャンペーンがあれば dsp-engine が入札を返す"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-bid", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        margin_rate=0.2, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
        creative_title="買ってね", creative_click_url="https://shop.example.com/lp",
    ))
    await db.commit()

    req = BidRequest(imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)])
    resp = await handle_bid_request(req, db)

    assert resp is not None
    assert resp.seatbid[0].seat == "dsp-engine"
    bid = resp.seatbid[0].bid[0]
    assert bid.price > 0
    assert bid.cid == "camp-bid"
    # ad markup のクリックリンクはクリック計測トラッカー経由
    assert "/dsp-engine/click?ct=" in bid.adm


@pytest.mark.asyncio
async def test_no_bid_when_no_active_campaign(db):
    """アクティブキャンペーンが無ければノービッド"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(id="camp-paused", status="paused"))
    await db.commit()

    req = BidRequest(imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)])
    assert await handle_bid_request(req, db) is None


@pytest.mark.asyncio
async def test_win_then_conversion_closes_roas_loop(db):
    """落札記録 → click_token 経由の購入CV → ROAS が成立する（計測ループ）"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-loop"))
    await db.commit()

    # 落札: 落札30 USD CPM → 30×150=4500円CPM → 1imp消化 = 4.5円
    log = await record_dsp_win(
        db, campaign_id="camp-loop", click_token="loop-ct", impression_id="imp-x",
        cleared_price_usd=30.0, bid_price_usd=33.0,
    )
    assert log.click_token == "loop-ct"
    assert abs(log.spend_jpy - 4.5) < 1e-6

    event, created = await record_conversion(db, click_token="loop-ct", revenue_jpy=9000.0)
    assert created is True
    assert event.campaign_id == "camp-loop"
    assert event.impression_id == "imp-x"

    roas = await get_campaign_roas(db, "camp-loop")
    assert roas["impressions"] == 1
    assert roas["conversions"] == 1
    assert abs(roas["revenue_jpy"] - 9000.0) < 1e-6
    assert abs(roas["spend_jpy"] - 4.5) < 1e-6


# ── Phase 2: 外部エクスチェンジ連携 ─────────────────────────────

def test_check_qps_under_limit():
    """QPS上限内なら入札を受け付ける"""
    from dsp_engine.exchange import check_qps
    assert all(check_qps("qps-under", 10) for _ in range(5))


def test_check_qps_blocks_over_limit():
    """同一秒で QPS 上限を超えたら False を返す"""
    from dsp_engine.exchange import check_qps
    results = [check_qps("qps-over", 3) for _ in range(5)]
    assert results[:3] == [True, True, True]
    assert results[3] is False and results[4] is False


def test_check_qps_unlimited():
    """qps_limit=0 は無制限"""
    from dsp_engine.exchange import check_qps
    assert all(check_qps("qps-unl", 0) for _ in range(50))


def test_currency_override_and_validation():
    """円/ドルレートの動的更新と不正値の拒否"""
    from dsp_engine.currency import get_jpy_per_usd, set_jpy_per_usd
    original = get_jpy_per_usd()
    set_jpy_per_usd(160.0)
    assert get_jpy_per_usd() == 160.0
    set_jpy_per_usd(0)     # 不正値は無視
    set_jpy_per_usd(-5)    # 不正値は無視
    assert get_jpy_per_usd() == 160.0
    set_jpy_per_usd(original)  # 後続テストへ影響しないよう戻す


@pytest.mark.asyncio
async def test_handle_bid_request_includes_nurl(db):
    """入札に落札通知URL(nurl)が含まれ、source がクエリに乗る"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request

    db.add(make_campaign(
        id="camp-nurl", base_ctr=0.1, target_cvr=0.1, avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
        creative_click_url="https://shop.example.com/lp",
    ))
    await db.commit()

    req = BidRequest(imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)])
    resp = await handle_bid_request(req, db, source="fluct-test")

    bid = resp.seatbid[0].bid[0]
    assert bid.nurl is not None
    assert "/dsp-engine/win" in bid.nurl
    assert "src=fluct-test" in bid.nurl
    assert "cid=camp-nurl" in bid.nurl
    assert "${AUCTION_PRICE}" in bid.nurl


@pytest.mark.asyncio
async def test_record_dsp_win_tags_external_source(db):
    """外部エクスチェンジ落札は source / platform が記録される"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-src"))
    await db.commit()

    log = await record_dsp_win(
        db, campaign_id="camp-src", click_token="src-ct", impression_id=None,
        cleared_price_usd=10.0, bid_price_usd=12.0,
        source="external-exch", platform="external",
    )
    assert log.source == "external-exch"
    assert log.platform == "external"


# ── クリック計測 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_click_logs_event(db):
    """クリックトラッカーがクリックイベントを1件記録する"""
    from db_models import DspClickEventDB
    from dsp_engine.attribution import record_click
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-clk"))
    await db.commit()
    await record_dsp_win(
        db, campaign_id="camp-clk", click_token="clk-1", impression_id="imp-1",
        cleared_price_usd=10.0, bid_price_usd=12.0,
    )
    log = await record_click(db, "clk-1")
    assert log is not None and log.campaign_id == "camp-clk"
    count = await db.scalar(
        select(func.count()).select_from(DspClickEventDB)
        .where(DspClickEventDB.click_token == "clk-1")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_click_unknown_token_returns_none(db):
    """未知の click_token なら None（落札ログ無し）"""
    from dsp_engine.attribution import record_click
    assert await record_click(db, "no-such-token") is None


@pytest.mark.asyncio
async def test_roas_includes_clicks_and_ctr(db):
    """ROAS サマリーに clicks と CTR(%) が含まれる"""
    from dsp_engine.attribution import get_campaign_roas, record_click
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-ctr"))
    await db.commit()
    for i in range(4):
        await record_dsp_win(
            db, campaign_id="camp-ctr", click_token=f"ctr-{i}", impression_id=f"imp-{i}",
            cleared_price_usd=10.0, bid_price_usd=10.0,
        )
    await record_click(db, "ctr-0")  # 4落札中1クリック

    roas = await get_campaign_roas(db, "camp-ctr")
    assert roas["impressions"] == 4
    assert roas["clicks"] == 1
    assert abs(roas["ctr"] - 25.0) < 1e-6  # 1/4 × 100


# ── 実MMP（AppsFlyer / Adjust）ポストバック形式の正規化 ─────────

def test_normalize_canonical_payload():
    """当社の標準パラメータ名をそのまま受ける"""
    from dsp_engine.attribution import normalize_conversion_payload
    n = normalize_conversion_payload({
        "dsp_ct": "tok", "revenue_jpy": "3000", "dedup_key": "d1", "event_type": "purchase",
    })
    assert n["click_token"] == "tok"
    assert n["revenue_jpy"] == 3000.0
    assert n["dedup_key"] == "d1"


def test_normalize_appsflyer_payload():
    """AppsFlyer 形式（click_id / event_revenue / event_name / event_id）を正規化"""
    from dsp_engine.attribution import normalize_conversion_payload
    n = normalize_conversion_payload({
        "click_id": "tok-af", "event_revenue": "8000", "event_revenue_currency": "JPY",
        "event_name": "af_purchase", "event_id": "afid-123",
    })
    assert n["click_token"] == "tok-af"
    assert n["revenue_jpy"] == 8000.0
    assert n["dedup_key"] == "afid-123"
    assert n["event_type"] == "af_purchase"


def test_normalize_adjust_payload_with_usd_conversion():
    """Adjust 形式（clickid / revenue+currency=USD / transaction_id）と USD→JPY 換算"""
    from dsp_engine.attribution import normalize_conversion_payload
    from dsp_engine.currency import set_jpy_per_usd
    set_jpy_per_usd(150.0)
    n = normalize_conversion_payload({
        "clickid": "tok-aj", "revenue": "50", "currency": "USD",
        "event": "purchase", "transaction_id": "tx-9",
    })
    assert n["click_token"] == "tok-aj"
    assert n["dedup_key"] == "tx-9"
    assert abs(n["revenue_jpy"] - 7500.0) < 1e-6  # 50 USD × 150


# ── Codex レビュー指摘の修正（クリック実数・クリック日集計・source明示） ──

@pytest.mark.asyncio
async def test_record_click_counts_every_click(db):
    """同一 click_token を2回クリックしたら clicks は 2（実クリック数・捨てない）"""
    from dsp_engine.attribution import get_campaign_roas, record_click
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-2clk"))
    await db.commit()
    await record_dsp_win(
        db, campaign_id="camp-2clk", click_token="dc-1", impression_id="i1",
        cleared_price_usd=10.0, bid_price_usd=10.0,
    )
    await record_click(db, "dc-1")
    await record_click(db, "dc-1")

    roas = await get_campaign_roas(db, "camp-2clk")
    assert roas["clicks"] == 2


@pytest.mark.asyncio
async def test_report_clicks_use_click_date_not_serve_date(db):
    """配信日とクリック日が別日でも、クリックはクリック発生日に計上される"""
    from datetime import date, datetime, timezone

    from db_models import DspClickEventDB, DspSpendLogDB
    from dsp_engine.reporting import run_report

    db.add(make_campaign(id="camp-day"))
    await db.commit()
    # 配信は 5/20、クリックは 5/22（日跨ぎ）
    db.add(DspSpendLogDB(
        id="sd-1", campaign_id="camp-day", click_token="d-ct",
        spend_jpy=100.0, cleared_price_jpy=100.0,
        logged_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc),
    ))
    db.add(DspClickEventDB(
        id="ce-1", campaign_id="camp-day", click_token="d-ct",
        clicked_at=datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc),
    ))
    await db.commit()

    rows = await run_report(
        db, date_from=date(2026, 5, 20), date_to=date(2026, 5, 23), dimensions=["day"]
    )
    by_day = {r["day"]: r for r in rows}
    assert by_day["2026-05-20"]["impressions"] == 1
    assert by_day["2026-05-20"]["clicks"] == 0   # 配信日にはクリックを出さない
    assert by_day["2026-05-22"]["clicks"] == 1   # クリック発生日に出す


def test_normalize_explicit_source_is_honored():
    """明示的な source パラメータは自動判定より優先される"""
    from dsp_engine.attribution import normalize_conversion_payload
    n = normalize_conversion_payload({
        "dsp_ct": "t", "revenue_jpy": "100", "source": "s2s_adjust",
    })
    assert n["source"] == "s2s_adjust"


def test_normalize_adjust_autodetect_by_adid():
    """Adjust 固有キー(adid)があれば source=s2s_adjust に自動判定"""
    from dsp_engine.attribution import normalize_conversion_payload
    n = normalize_conversion_payload({
        "clickid": "t", "revenue": "100", "adid": "adj-device-1",
    })
    assert n["source"] == "s2s_adjust"


# ── アドテクレビュー改善（認証 / 総予算 / N+1 / 冪等 / 期間） ───

def test_verify_exchange_secret_no_secret_required():
    """api_secret 未設定のエクスチェンジは認証不要（常に True）"""
    from db_models import DspConfigDB
    from dsp_engine.exchange import verify_exchange_secret
    exch = DspConfigDB(name="x", endpoint_url="u", api_secret=None)
    assert verify_exchange_secret(exch, None) is True
    assert verify_exchange_secret(exch, "anything") is True


def test_verify_exchange_secret_enforced():
    """api_secret 設定時はヘッダー一致を要求する"""
    from db_models import DspConfigDB
    from dsp_engine.exchange import verify_exchange_secret
    exch = DspConfigDB(name="x", endpoint_url="u", api_secret="s3cret")
    assert verify_exchange_secret(exch, "s3cret") is True
    assert verify_exchange_secret(exch, "wrong") is False
    assert verify_exchange_secret(exch, None) is False


@pytest.mark.asyncio
async def test_can_bid_blocks_when_total_budget_exhausted():
    """総予算（total_budget_jpy）を消化しきったら入札不可"""
    from dsp_engine.pacing import BudgetPacer
    pacer = BudgetPacer()
    c = make_campaign(id="camp-tb", daily_budget_jpy=0.0, total_budget_jpy=10_000.0)
    now = datetime(2026, 5, 22, 12, 0, 0)
    assert await pacer.can_bid(c, lifetime_spend_jpy=9_999.0, now=now) is True
    assert await pacer.can_bid(c, lifetime_spend_jpy=10_000.0, now=now) is False


@pytest.mark.asyncio
async def test_can_bid_total_budget_zero_is_unlimited():
    """total_budget_jpy=0 は総予算無制限"""
    from dsp_engine.pacing import BudgetPacer
    pacer = BudgetPacer()
    c = make_campaign(id="camp-tb0", daily_budget_jpy=0.0, total_budget_jpy=0.0)
    assert await pacer.can_bid(
        c, lifetime_spend_jpy=9_999_999.0, now=datetime(2026, 5, 22, 3, 0, 0)
    ) is True


@pytest.mark.asyncio
async def test_get_all_campaign_stats_batch(db):
    """全キャンペーンの実績を一括取得（入札パスの N+1 解消）"""
    from dsp_engine.attribution import record_conversion
    from dsp_engine.bidder import record_dsp_win
    from dsp_engine.campaign_manager import get_all_campaign_stats

    db.add_all([make_campaign(id="bc-1"), make_campaign(id="bc-2")])
    await db.commit()
    await record_dsp_win(
        db, campaign_id="bc-1", click_token="b1", impression_id="i1",
        cleared_price_usd=10.0, bid_price_usd=10.0,
    )
    await record_conversion(db, campaign_id="bc-2", revenue_jpy=5000.0, dedup_key="bcv1")

    stats = await get_all_campaign_stats(db, ["bc-1", "bc-2"])
    assert stats["bc-1"]["impressions"] == 1
    assert stats["bc-1"]["revenue_jpy"] == 0.0
    assert stats["bc-2"]["revenue_jpy"] == 5000.0
    assert stats["bc-2"]["impressions"] == 0


@pytest.mark.asyncio
async def test_record_dsp_win_idempotent_by_click_token(db):
    """同一 click_token の落札記録（nurl再送）は二重計上されない"""
    from db_models import DspSpendLogDB
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-idem-win"))
    await db.commit()
    await record_dsp_win(
        db, campaign_id="camp-idem-win", click_token="win-ct", impression_id="i1",
        cleared_price_usd=10.0, bid_price_usd=10.0,
    )
    await record_dsp_win(
        db, campaign_id="camp-idem-win", click_token="win-ct", impression_id="i1",
        cleared_price_usd=10.0, bid_price_usd=10.0,
    )
    count = await db.scalar(
        select(func.count()).select_from(DspSpendLogDB)
        .where(DspSpendLogDB.click_token == "win-ct")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_list_active_campaigns_excludes_out_of_period(db):
    """配信期間外（start_date 未来 / end_date 過去）のキャンペーンは入札対象外"""
    from datetime import timedelta

    from dsp_engine.campaign_manager import list_active_campaigns
    from utils import utcnow

    today = utcnow().date()  # 実装(list_active_campaigns)と同じ UTC 基準に揃える
    db.add_all([
        make_campaign(id="cd-live", status="active",
                      start_date=today - timedelta(days=1), end_date=today + timedelta(days=1)),
        make_campaign(id="cd-expired", status="active",
                      start_date=today - timedelta(days=10), end_date=today - timedelta(days=1)),
        make_campaign(id="cd-future", status="active",
                      start_date=today + timedelta(days=5), end_date=None),
        make_campaign(id="cd-nodate", status="active"),  # 期間指定なし → 常に有効
    ])
    await db.commit()

    ids = {c.id for c in await list_active_campaigns(db)}
    assert "cd-live" in ids
    assert "cd-nodate" in ids
    assert "cd-expired" not in ids
    assert "cd-future" not in ids


# ── first-price bid shading（優先タスク #2）─────────────────────

@pytest.mark.asyncio
async def test_handle_bid_request_at1_applies_shading(db):
    """at=1 (first-price): 過去落札の P50 で bid shading され入札額が下がる"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request
    from dsp_engine.currency import get_jpy_per_usd

    db.add(make_campaign(
        id="camp-shade", base_ctr=0.01, target_cvr=0.02, avg_purchase_value_jpy=3000.0,
        margin_rate=0.20, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    # 過去落札 12 件（cleared=120円 < raw 480円）→ shading が効く
    for i in range(12):
        db.add(DspSpendLogDB(
            campaign_id="camp-shade", click_token=f"sh-{i}", cleared_price_jpy=120.0,
        ))
    await db.commit()

    imp = Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)
    resp_first = await handle_bid_request(BidRequest(imp=[imp], at=1), db)
    resp_second = await handle_bid_request(BidRequest(imp=[imp], at=2), db)

    assert resp_first is not None and resp_second is not None
    rate = get_jpy_per_usd()
    price_first = resp_first.seatbid[0].bid[0].price
    price_second = resp_second.seatbid[0].bid[0].price
    # at=2 は shading 非適用 → scoring の raw 入札。
    # #5 shrinkage: 12imp/0click が観測 CTR=0% として pCTR を prior から引き下げる。
    from dsp_engine.scoring import compute_bid_cpm_jpy
    expected_raw = compute_bid_cpm_jpy(make_campaign(
        id="camp-shade-ref", base_ctr=0.01, target_cvr=0.02, avg_purchase_value_jpy=3000.0,
        margin_rate=0.20, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ), {"impressions": 12, "clicks": 0, "conversions": 0, "revenue_jpy": 0.0})
    assert abs(price_first - 120.0 / rate) < 1e-6        # shaded（過去落札 P50 = 120円）
    assert abs(price_second - expected_raw / rate) < 1e-6  # second-price は raw フルプライス
    assert price_first < price_second


# ── dsp #10 phase 1: 複合インデックス存在検証 (Red) ──────────────

@pytest.mark.asyncio
async def test_composite_indexes_exist(db):
    """
    5 本の複合インデックスが DB に存在し、かつカラム構成が正しいことを検証する。

    期待するインデックス:
      1. dsp_spend_logs        : ix_dsp_spend_logs_campaign_logged         (campaign_id, logged_at)
      2. dsp_click_events      : ix_dsp_click_events_campaign_clicked       (campaign_id, clicked_at)
      3. dsp_conversion_events : ix_dsp_conv_events_campaign_attributed_received
                                   (campaign_id, attributed, received_at)
      4. dsp_bid_logs          : ix_dsp_bid_logs_outcome_campaign           (outcome, campaign_id)
      5. dsp_bid_logs          : ix_dsp_bid_logs_campaign_nbr_logged        (campaign_id, nbr, logged_at)

    これらはまだ db_models.py に定義されていないため FAIL する (Red)。
    """
    from sqlalchemy import text

    expected = [
        ("dsp_spend_logs",        "ix_dsp_spend_logs_campaign_logged",               ["campaign_id", "logged_at"]),
        ("dsp_click_events",      "ix_dsp_click_events_campaign_clicked",             ["campaign_id", "clicked_at"]),
        ("dsp_conversion_events", "ix_dsp_conv_events_campaign_attributed_received",  ["campaign_id", "attributed", "received_at"]),
        ("dsp_bid_logs",          "ix_dsp_bid_logs_outcome_campaign",                 ["outcome", "campaign_id"]),
        ("dsp_bid_logs",          "ix_dsp_bid_logs_campaign_nbr_logged",              ["campaign_id", "nbr", "logged_at"]),
    ]

    for table, index_name, expected_cols in expected:
        # PRAGMA index_list('<table>') でインデックス一覧を取得
        rows = (await db.execute(text(f"PRAGMA index_list('{table}')"))).fetchall()
        index_names = [r[1] for r in rows]  # col 1 = index name
        assert index_name in index_names, (
            f"インデックス '{index_name}' が '{table}' に存在しない。"
            f"現在の index_list: {index_names}"
        )

        # PRAGMA index_info('<index>') でカラム構成を確認 (col 2 = column name)
        info_rows = (await db.execute(text(f"PRAGMA index_info('{index_name}')"))).fetchall()
        actual_cols = [r[2] for r in info_rows]  # seqno 順 (= index_rank 順)
        assert actual_cols == expected_cols, (
            f"インデックス '{index_name}' のカラム構成が不一致。"
            f"expected={expected_cols}, actual={actual_cols}"
        )


@pytest.mark.asyncio
async def test_handle_bid_request_at2_no_shading(db):
    """at=2 (second-price): 過去落札が多くても shading されずフルプライス入札"""
    from auction.openrtb import Banner, BidRequest, Impression
    from dsp_engine.bidder import handle_bid_request
    from dsp_engine.currency import get_jpy_per_usd

    db.add(make_campaign(
        id="camp-ns", base_ctr=0.01, target_cvr=0.02, avg_purchase_value_jpy=3000.0,
        margin_rate=0.20, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ))
    for i in range(12):
        db.add(DspSpendLogDB(
            campaign_id="camp-ns", click_token=f"ns-{i}", cleared_price_jpy=120.0,
        ))
    await db.commit()

    imp = Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)
    resp = await handle_bid_request(BidRequest(imp=[imp], at=2), db)

    assert resp is not None
    rate = get_jpy_per_usd()
    # at=2 は shading 非適用 → scoring の raw 入札（#5 shrinkage: 12imp/0click が pCTR を引き下げる）
    from dsp_engine.scoring import compute_bid_cpm_jpy
    expected_raw = compute_bid_cpm_jpy(make_campaign(
        id="camp-ns-ref", base_ctr=0.01, target_cvr=0.02, avg_purchase_value_jpy=3000.0,
        margin_rate=0.20, bid_floor_jpy=100.0, bid_cap_jpy=100_000.0,
    ), {"impressions": 12, "clicks": 0, "conversions": 0, "revenue_jpy": 0.0})
    assert abs(resp.seatbid[0].bid[0].price - expected_raw / rate) < 1e-6


# ── win notice 署名（レビュー指摘3: nurl 無署名による spend 偽装の防止）──

def test_sign_verify_win_notice_roundtrip():
    """正しく署名した win notice は検証を通る"""
    from dsp_engine.bidder import sign_win_notice, verify_win_notice

    sig = sign_win_notice(ct="ct1", cid="camp-1", src="exch", bid=1.5)
    assert verify_win_notice(sig, ct="ct1", cid="camp-1", src="exch", bid=1.5) is True


def test_verify_win_notice_rejects_tampered_cid():
    """cid を改竄した win notice は検証で弾く（任意 campaign への spend 偽装を阻止）"""
    from dsp_engine.bidder import sign_win_notice, verify_win_notice

    sig = sign_win_notice(ct="ct1", cid="camp-1", src="exch", bid=1.5)
    assert verify_win_notice(sig, ct="ct1", cid="camp-EVIL", src="exch", bid=1.5) is False


def test_verify_win_notice_rejects_missing_sig():
    """署名なし（空文字 / None）は検証 NG"""
    from dsp_engine.bidder import verify_win_notice

    assert verify_win_notice("", ct="ct1", cid="camp-1", src="exch", bid=1.5) is False
    assert verify_win_notice(None, ct="ct1", cid="camp-1", src="exch", bid=1.5) is False


def test_win_notice_url_includes_valid_signature():
    """win_notice_url が生成する nurl は検証可能な sig を含む"""
    import urllib.parse

    from dsp_engine.bidder import verify_win_notice, win_notice_url

    url = win_notice_url("camp-1", "ct1", "exch", 1.5)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert "sig" in qs
    ok = verify_win_notice(
        qs["sig"][0], ct="ct1", cid="camp-1", src="exch", bid=float(qs["bid"][0])
    )
    assert ok is True


@pytest_asyncio.fixture
async def win_client():
    """win notice エンドポイント用の自己完結 HTTP クライアント。

    conftest の module-scoped `client` はファイルDB(test_mdm_temp.db)を使い、
    Windows で他モジュールとファイルロック競合（PermissionError）を起こすため、
    :memory: + StaticPool でこのモジュール内に隔離する。
    """
    from httpx import ASGITransport, AsyncClient

    from database import get_db
    from main import app

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_win_endpoint_rejects_unsigned_request(win_client):
    """署名なしの win notice 受信は 403（spend 計上させない）"""
    resp = await win_client.get(
        "/dsp-engine/win",
        params={"ct": "x", "cid": "camp-1", "src": "exch", "bid": 1.0, "price": "999"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_win_endpoint_rejects_forged_signature(win_client):
    """偽造署名の win notice 受信も 403"""
    resp = await win_client.get(
        "/dsp-engine/win",
        params={"ct": "x", "cid": "camp-1", "src": "exch", "bid": 1.0,
                "price": "999", "sig": "deadbeefdeadbeef"},
    )
    assert resp.status_code == 403


# ── セキュリティ修正 3 件 (Red: 実装前の再現テスト) ─────────────────

@pytest.mark.asyncio
async def test_conversion_click_token_overrides_mismatched_campaign_id(db):
    """click_token が解決できたら、リクエストの campaign_id より spend_log の
    campaign_id を優先する（別キャンペーンへの売上付け替えを防ぐ）。"""
    db.add(make_campaign(id="camp-true"))
    db.add(make_campaign(id="camp-attacker"))
    await db.commit()
    db.add(DspSpendLogDB(
        id="spend-true", campaign_id="camp-true", click_token="ct-true",
        cleared_price_jpy=200.0, spend_jpy=0.2,
    ))
    await db.commit()

    # 攻撃者は本物の click_token と別キャンペーンIDを同時に渡す
    event, created = await record_conversion(
        db, campaign_id="camp-attacker", click_token="ct-true", revenue_jpy=99999.0,
    )
    assert created is True
    assert event.campaign_id == "camp-true"  # spend_log の campaign_id を採用


def test_verify_win_notice_rejects_tampered_crid():
    """crid を改竄した win notice は検証で弾く（creative 軸レポートの改竄を阻止）。"""
    from dsp_engine.bidder import sign_win_notice, verify_win_notice

    sig = sign_win_notice(ct="ct1", cid="camp-1", src="exch", bid=1.5, crid="cr-real")
    assert verify_win_notice(
        sig, ct="ct1", cid="camp-1", src="exch", bid=1.5, crid="cr-real"
    ) is True
    assert verify_win_notice(
        sig, ct="ct1", cid="camp-1", src="exch", bid=1.5, crid="cr-EVIL"
    ) is False


@pytest.mark.asyncio
async def test_can_bid_blocks_on_db_daily_spend_when_counter_lost():
    """Redis/メモリカウンタが消えても、DB の当日実績で日予算ペース超過を
    検知して入札を止める（Redis flush / プロセス再起動を模擬）。"""
    pacer = BudgetPacer()
    now = datetime(2026, 5, 21, 12, 0, 0)
    c = make_campaign(id="camp-dbfb", daily_budget_jpy=24000.0)
    # record_spend を呼ばない = Redis/メモリカウンタは 0
    # DB 実績 11000 はペース許容 12000 × 0.9 = 10800 を超える
    assert await pacer.can_bid(c, daily_spend_jpy=11000.0, now=now) is False


# ── Phase 2: admin/campaigns N+1 再現テスト ─────────────────────────────────

@pytest.mark.asyncio
async def test_admin_campaigns_no_n_plus_1(db):
    """GET /dsp-engine/admin/campaigns が N キャンペーンでも get_campaign_roas を
    一切呼ばず、get_all_campaign_stats を 1 回だけ呼ぶことを検証する (Red: 現状は N+1)。

    現状の admin_campaigns_page はキャンペーン N 件に対し get_campaign_roas を N 回呼ぶ。
    このテストはその N+1 を AssertionError で検出し FAIL する (=再現テスト)。
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.responses import Response
    from starlette.requests import Request

    from dsp_engine import campaign_manager as _cm
    from dsp_engine.router import admin_campaigns_page

    # 3 キャンペーンを seed
    db.add_all([
        make_campaign(id="np1-1", status="active", daily_budget_jpy=1000.0),
        make_campaign(id="np1-2", status="active", daily_budget_jpy=2000.0),
        make_campaign(id="np1-3", status="active", daily_budget_jpy=3000.0),
    ])
    await db.commit()

    # テンプレート描画を回避するためダミーの TemplateResponse を patch
    mock_template_response = MagicMock(return_value=Response("ok"))

    # get_campaign_roas を側効果 AssertionError で patch
    # → admin_campaigns_page がこれを 1 回でも呼んだら AssertionError が raise されて test FAIL
    mock_roas = AsyncMock(
        side_effect=AssertionError(
            "N+1: get_campaign_roas should not be called for list view"
        )
    )

    # get_all_campaign_stats を wraps で patch して呼び出し回数を検証
    mock_stats = AsyncMock(wraps=_cm.get_all_campaign_stats)

    with (
        patch("dsp_engine.router.get_campaign_roas", mock_roas),
        patch("dsp_engine.campaign_manager.get_all_campaign_stats", mock_stats),
        patch("dsp_engine.router.templates.TemplateResponse", mock_template_response),
    ):
        dummy_request = MagicMock(spec=Request)
        await admin_campaigns_page(request=dummy_request, db=db)

    # get_campaign_roas は 0 回であること (mock_roas.side_effect で FAIL するが念のため)
    assert mock_roas.call_count == 0, (
        f"get_campaign_roas が {mock_roas.call_count} 回呼ばれた (N+1 クエリ)"
    )

    # get_all_campaign_stats は 1 回だけ呼ばれること
    assert mock_stats.call_count == 1, (
        f"get_all_campaign_stats の呼び出し回数が {mock_stats.call_count} 回 (expected 1)"
    )

    # テンプレートに渡された rows の各要素の roas dict に必須キーが含まれること
    assert mock_template_response.call_count == 1
    call_kwargs = mock_template_response.call_args
    context = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]
    rows = context["rows"]
    required_keys = {"impressions", "spend_jpy", "conversions", "revenue_jpy", "roas", "ctr", "cpa"}
    for row in rows:
        missing = required_keys - set(row["roas"].keys())
        assert not missing, f"roas dict にキーが不足: {missing}"
