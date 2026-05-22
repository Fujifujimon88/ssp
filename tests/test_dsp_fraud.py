"""
dsp_engine #8 — fraud / IVT / brand safety 監視 の Red フェーズテスト。

TDD Red フェーズ: 実装前に先に書く失敗テスト。検証対象:
  - クリック連打レート制限（token 単位 / IP 単位 / 独立性 / DB 非記録）
  - revenue 検証（負値を弾く / 外れ値を弾く / 正常値は通す）
  - IVT 判定（datacenter IP / bot UA / 正常リクエスト）
  - handle_bid_request の IVT no-bid（dsp_ivt_strict=True 時に NBR_IVT_DETECTED=506）
  - brand safety（bcat 一致でブロック / badv 一致でブロック / 非一致で通過）
  - 全キャンペーンが brand safety でブロックされた時の no-bid（NBR_BRAND_SAFETY_BLOCK=507）

新規モジュール dsp_engine/fraud.py の関数を import して使う想定。
想定 public API:
  - check_click_rate_limit(redis, click_token, client_ip, *, token_limit, ip_limit, window_seconds) -> bool
  - validate_revenue(revenue_jpy, *, avg_purchase_value_jpy, revenue_cap_multiplier) -> bool
  - is_ivt(client_ip, user_agent, *, datacenter_cidrs) -> bool
  - is_brand_safety_blocked(bid_request, campaign) -> bool

実行: cd ssp_platform && python -m pytest tests/test_dsp_fraud.py -v
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database import Base
from db_models import (
    DspBidLogDB,
    DspCampaignDB,
    DspClickEventDB,
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


def make_spend(campaign_id: str, click_token: str) -> DspSpendLogDB:
    return DspSpendLogDB(
        campaign_id=campaign_id,
        click_token=click_token,
        impression_id="imp-x",
        platform="web",
        source="ssp-node",
        bid_price_jpy=0.0,
        cleared_price_jpy=0.0,
        spend_jpy=1.0,
    )


# ── クリック連打レート制限（token / IP / 独立性 / DB 非記録） ──────


def test_click_rate_limit_token_exceeds_limit():
    """同一 click_token が閾値を超えるとレート制限 True を返す"""
    from dsp_engine.fraud import check_click_rate_limit

    # redis=None はメモリフォールバック想定
    result = check_click_rate_limit(
        None,
        click_token="token-flood",
        client_ip="1.2.3.4",
        token_limit=3,
        ip_limit=100,
        window_seconds=3600,
        _override_token_count=4,   # token 超過を注入
        _override_ip_count=1,
    )
    assert result is True, "token 単位でレート制限を超えたら True を返す"


def test_click_rate_limit_ip_exceeds_limit():
    """同一 client_ip が閾値を超えるとレート制限 True を返す"""
    from dsp_engine.fraud import check_click_rate_limit

    result = check_click_rate_limit(
        None,
        click_token="token-ok",
        client_ip="192.0.2.1",
        token_limit=100,
        ip_limit=5,
        window_seconds=3600,
        _override_token_count=1,
        _override_ip_count=6,      # IP 超過を注入
    )
    assert result is True, "IP 単位でレート制限を超えたら True を返す"


def test_click_rate_limit_token_and_ip_independent():
    """token/IP のどちらか一方が超えても、両方が閾値内なら制限しない"""
    from dsp_engine.fraud import check_click_rate_limit

    result = check_click_rate_limit(
        None,
        click_token="token-fine",
        client_ip="10.0.0.1",
        token_limit=5,
        ip_limit=10,
        window_seconds=3600,
        _override_token_count=2,   # token: 閾値内
        _override_ip_count=3,      # IP: 閾値内
    )
    assert result is False, "token/IP ともに閾値内なら制限なし(False)"


@pytest.mark.asyncio
async def test_click_rate_limited_request_does_not_record_click_event(db):
    """レート制限時は DspClickEventDB を記録しない"""
    from dsp_engine.attribution import record_click

    # spend log は存在する（クリック記録のトリガー）
    db.add(make_campaign(id="camp-rl"))
    db.add(make_spend("camp-rl", "ct-rl"))
    await db.commit()

    # 通常クリック: 記録される
    result = await record_click(db, "ct-rl", rate_limited=False)
    assert result is not None

    # レート制限フラグ付き: DspClickEventDB に追記しない
    result_limited = await record_click(db, "ct-rl", rate_limited=True)
    # rate_limited=True のとき record_click は DB 記録をスキップして None/同じログを返す仕様
    all_events = (await db.scalars(
        select(DspClickEventDB).where(DspClickEventDB.click_token == "ct-rl")
    )).all()
    # rate_limited=True の後 click event が増えていないことを確認
    assert len(all_events) == 1, "レート制限時は DspClickEventDB を追記しない"


# ── revenue 検証（負値 / 外れ値 / 正常値） ───────────────────────


def test_validate_revenue_rejects_negative():
    """revenue_jpy が負値なら False を返す"""
    from dsp_engine.fraud import validate_revenue

    assert validate_revenue(
        -1.0,
        avg_purchase_value_jpy=10_000.0,
        revenue_cap_multiplier=10.0,
    ) is False, "負の revenue は拒否"


def test_validate_revenue_rejects_outlier():
    """avg_purchase_value_jpy * cap_multiplier を超える revenue_jpy は False を返す"""
    from dsp_engine.fraud import validate_revenue

    # avg=10000, multiplier=10 → 上限 100000
    assert validate_revenue(
        100_001.0,
        avg_purchase_value_jpy=10_000.0,
        revenue_cap_multiplier=10.0,
    ) is False, "上限倍率超過の revenue は拒否"


def test_validate_revenue_accepts_normal():
    """正常な revenue_jpy（0以上かつ上限以内）は True を返す"""
    from dsp_engine.fraud import validate_revenue

    assert validate_revenue(
        5_000.0,
        avg_purchase_value_jpy=10_000.0,
        revenue_cap_multiplier=10.0,
    ) is True, "正常値は通す"


def test_validate_revenue_accepts_zero():
    """revenue_jpy=0 は有効（purchase 以外のイベントも対象）"""
    from dsp_engine.fraud import validate_revenue

    assert validate_revenue(
        0.0,
        avg_purchase_value_jpy=10_000.0,
        revenue_cap_multiplier=10.0,
    ) is True, "0 は有効"


# ── IVT 判定（datacenter IP / bot UA / 正常） ────────────────────


def test_is_ivt_datacenter_ip_detected():
    """datacenter_cidrs に一致する IP は IVT (True)"""
    from dsp_engine.fraud import is_ivt

    result = is_ivt(
        client_ip="203.0.113.5",
        user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120",
        datacenter_cidrs=["203.0.113.0/24"],
    )
    assert result is True, "datacenter CIDR 内 IP は IVT"


def test_is_ivt_bot_user_agent_detected():
    """既知 bot UA シグネチャは IVT (True)"""
    from dsp_engine.fraud import is_ivt

    result = is_ivt(
        client_ip="8.8.8.8",
        user_agent="Googlebot/2.1 (+http://www.google.com/bot.html)",
        datacenter_cidrs=[],
    )
    assert result is True, "bot UA は IVT"


def test_is_ivt_normal_request_not_ivt():
    """正常なブラウザ UA かつ datacenter 外 IP は IVT ではない (False)"""
    from dsp_engine.fraud import is_ivt

    result = is_ivt(
        client_ip="203.0.112.1",   # CIDR 外
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15",
        datacenter_cidrs=["203.0.113.0/24"],
    )
    assert result is False, "通常の iOS UA は IVT でない"


# ── handle_bid_request の IVT no-bid（NBR 506） ──────────────────


@pytest.mark.asyncio
async def test_handle_bid_request_ivt_strict_no_bid(db):
    """dsp_ivt_strict=True のとき IVT リクエストは no-bid (NBR_IVT_DETECTED=506) になる"""
    from auction.openrtb import Banner, BidRequest, Device, Impression
    from dsp_engine.bidder import handle_bid_request
    from dsp_engine.nbr import NBR_IVT_DETECTED

    db.add(make_campaign(id="camp-ivt"))
    await db.commit()

    # bot UA を持つ BidRequest
    req = BidRequest(
        id="req-ivt",
        imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)],
        device=Device(ua="Googlebot/2.1 (+http://www.google.com/bot.html)", ip="8.8.8.8"),
    )
    resp = await handle_bid_request(req, db)
    assert resp is None, "IVT strict モードでは no-bid"

    # NBR_IVT_DETECTED(506) で記録されていること
    logs = (await db.scalars(
        select(DspBidLogDB).where(DspBidLogDB.nbr == NBR_IVT_DETECTED)
    )).all()
    assert len(logs) >= 1, "NBR 506 (IVT_DETECTED) が bid log に記録される"


# ── brand safety（bcat / badv 一致でブロック / 非一致で通過） ────


def test_brand_safety_bcat_match_blocks():
    """BidRequest の site.cat がキャンペーンの bcat_block と一致したらブロック (True)"""
    from dsp_engine.fraud import is_brand_safety_blocked

    # campaign に bcat_block を持たせる（#8 で追加予定のフィールド）
    campaign = make_campaign(id="camp-bcat", bcat_block='["IAB25", "IAB26"]')

    from auction.openrtb import BidRequest, Site
    req = BidRequest(
        id="req-bcat",
        imp=[],
        site=Site(cat=["IAB25-3"], page="https://example.com"),
    )
    result = is_brand_safety_blocked(req, campaign)
    assert result is True, "site.cat が bcat_block と一致したらブロック"


def test_brand_safety_badv_match_blocks():
    """BidRequest の app/site に競合ドメインが含まれたらブロック (True)"""
    from dsp_engine.fraud import is_brand_safety_blocked

    campaign = make_campaign(
        id="camp-badv",
        bcat_block="[]",
        badv_block='["rival.example.com"]',
    )

    from auction.openrtb import BidRequest, Site
    req = BidRequest(
        id="req-badv",
        imp=[],
        site=Site(domain="rival.example.com", page="https://rival.example.com/page"),
    )
    result = is_brand_safety_blocked(req, campaign)
    assert result is True, "badv ドメインと一致したらブロック"


def test_brand_safety_no_match_passes():
    """bcat も badv も一致しなければブロックしない (False)"""
    from dsp_engine.fraud import is_brand_safety_blocked

    campaign = make_campaign(
        id="camp-safe",
        bcat_block='["IAB25"]',
        badv_block='["rival.example.com"]',
    )

    from auction.openrtb import BidRequest, Site
    req = BidRequest(
        id="req-safe",
        imp=[],
        site=Site(cat=["IAB1"], domain="safe.example.com", page="https://safe.example.com/"),
    )
    result = is_brand_safety_blocked(req, campaign)
    assert result is False, "bcat/badv 非一致なら通過"


# ── 全キャンペーンが brand safety でブロックされた時の no-bid（NBR 507） ──


@pytest.mark.asyncio
async def test_handle_bid_request_all_brand_safety_blocked_no_bid(db):
    """全キャンペーンが brand safety でブロックされたら no-bid (NBR_BRAND_SAFETY_BLOCK=507)"""
    from auction.openrtb import Banner, BidRequest, Impression, Site
    from dsp_engine.bidder import handle_bid_request
    from dsp_engine.nbr import NBR_BRAND_SAFETY_BLOCK

    # キャンペーンに bcat_block を設定（site.cat="IAB25" 系をブロック）
    db.add(make_campaign(
        id="camp-bs",
        bcat_block='["IAB25"]',
        badv_block="[]",
    ))
    await db.commit()

    req = BidRequest(
        id="req-bs",
        imp=[Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.0)],
        site=Site(cat=["IAB25-1"], page="https://example.com/adult"),
    )
    resp = await handle_bid_request(req, db)
    assert resp is None, "全キャンペーンが brand safety ブロックなら no-bid"

    logs = (await db.scalars(
        select(DspBidLogDB).where(DspBidLogDB.nbr == NBR_BRAND_SAFETY_BLOCK)
    )).all()
    assert len(logs) >= 1, "NBR 507 (BRAND_SAFETY_BLOCK) が bid log に記録される"
