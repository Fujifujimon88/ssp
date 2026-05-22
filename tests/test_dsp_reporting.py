"""
#6 多次元レポート拡張のテスト。

検証対象:
  - extract_report_dims : BidRequest から publisher/app/placement/geo/deal_id 抽出
  - record_dsp_win      : spend log に 6 軸（creative は campaign 由来）を記録
  - record_click        : click event に spend log の 6 軸をコピー
  - record_conversion   : conversion event に spend log の 6 軸をコピー
  - run_report          : 新軸での GROUP BY 集計 / 既存軸の非破壊

実行: cd ssp_platform && pytest tests/test_dsp_reporting.py -v
"""
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspCampaignDB, DspClickEventDB, DspConversionEventDB, DspSpendLogDB


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
        creative_id="cre-default",
        creative_title="買ってね",
        creative_click_url="https://advertiser.example.com/lp",
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


def make_bid_request(*, publisher_id="pub-1", app_id=None, tagid="slot-1",
                     country="JPN", deal_id=None):
    """site / app どちらかの BidRequest を組み立てる。"""
    from auction.openrtb import (
        App, Banner, BidRequest, Deal, Device, Geo, Impression, Pmp, Publisher, Site,
    )
    pmp = Pmp(deals=[Deal(id=deal_id)]) if deal_id else None
    imp = Impression(id="imp-1", banner=Banner(w=300, h=250), tagid=tagid, pmp=pmp)
    site = app = None
    if app_id:
        app = App(id=app_id, publisher=Publisher(id=publisher_id))
    else:
        site = Site(id=publisher_id, publisher=Publisher(id=publisher_id))
    device = Device(geo=Geo(country=country)) if country else None
    return BidRequest(imp=[imp], site=site, app=app, device=device)


# ── extract_report_dims ────────────────────────────────────────

def test_extract_report_dims_site():
    """site 経由の BidRequest から publisher/placement/geo/deal_id を抽出する"""
    from dsp_engine.reporting import extract_report_dims

    dims = extract_report_dims(
        make_bid_request(publisher_id="pub-A", tagid="slot-X",
                         country="JPN", deal_id="deal-9")
    )
    assert dims["publisher_id"] == "pub-A"
    assert dims["placement"] == "slot-X"
    assert dims["geo"] == "JPN"
    assert dims["deal_id"] == "deal-9"
    assert dims["app_id"] is None


def test_extract_report_dims_app():
    """app 経由の BidRequest からは app_id が取れる"""
    from dsp_engine.reporting import extract_report_dims

    dims = extract_report_dims(
        make_bid_request(publisher_id="pub-B", app_id="com.example.app", country="USA")
    )
    assert dims["app_id"] == "com.example.app"
    assert dims["publisher_id"] == "pub-B"
    assert dims["geo"] == "USA"


def test_extract_report_dims_none():
    """bid_request=None は全軸 None を返す（外部エクスチェンジ経路）"""
    from dsp_engine.reporting import extract_report_dims

    dims = extract_report_dims(None)
    assert set(dims.keys()) == {"publisher_id", "app_id", "placement", "geo", "deal_id"}
    assert all(v is None for v in dims.values())


def test_available_dimensions_extended():
    """AVAILABLE_DIMENSIONS に 6 軸が追加されている"""
    from dsp_engine.reporting import AVAILABLE_DIMENSIONS

    for d in ["creative", "publisher", "app", "placement", "geo", "deal_id"]:
        assert d in AVAILABLE_DIMENSIONS


# ── record_dsp_win: 6 軸の記録 ─────────────────────────────────

@pytest.mark.asyncio
async def test_record_dsp_win_records_dims(db):
    """record_dsp_win が spend log に 6 軸を記録する（creative は campaign 由来）"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-d", creative_id="cre-77"))
    await db.commit()

    await record_dsp_win(
        db, campaign_id="camp-d", click_token="ct-d1", impression_id="i1",
        cleared_price_usd=30.0, bid_price_usd=33.0,
        bid_request=make_bid_request(publisher_id="pub-Z", tagid="slot-7",
                                     country="JPN", deal_id="deal-1"),
    )
    spend = await db.scalar(
        select(DspSpendLogDB).where(DspSpendLogDB.click_token == "ct-d1")
    )
    assert spend.creative_id == "cre-77"
    assert spend.publisher_id == "pub-Z"
    assert spend.placement == "slot-7"
    assert spend.geo == "JPN"
    assert spend.deal_id == "deal-1"


@pytest.mark.asyncio
async def test_record_dsp_win_without_bid_request(db):
    """bid_request 無しでも creative は campaign から解決し、他軸は null 記録"""
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-n", creative_id="cre-n"))
    await db.commit()

    await record_dsp_win(
        db, campaign_id="camp-n", click_token="ct-n1", impression_id="i1",
        cleared_price_usd=10.0, bid_price_usd=11.0,
    )
    spend = await db.scalar(
        select(DspSpendLogDB).where(DspSpendLogDB.click_token == "ct-n1")
    )
    assert spend.creative_id == "cre-n"
    assert spend.publisher_id is None
    assert spend.placement is None


# ── record_click / record_conversion: 軸のコピー ───────────────

@pytest.mark.asyncio
async def test_record_click_copies_dims(db):
    """record_click が spend log の 6 軸を click event へコピーする"""
    from dsp_engine.attribution import record_click
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-c", creative_id="cre-c"))
    await db.commit()
    await record_dsp_win(
        db, campaign_id="camp-c", click_token="ct-c1", impression_id="i1",
        cleared_price_usd=20.0, bid_price_usd=22.0,
        bid_request=make_bid_request(publisher_id="pub-C", tagid="slot-C", country="JPN"),
    )

    await record_click(db, "ct-c1")
    click = await db.scalar(
        select(DspClickEventDB).where(DspClickEventDB.click_token == "ct-c1")
    )
    assert click.creative_id == "cre-c"
    assert click.publisher_id == "pub-C"
    assert click.placement == "slot-C"
    assert click.geo == "JPN"


@pytest.mark.asyncio
async def test_record_conversion_copies_dims(db):
    """record_conversion が spend log の 6 軸を conversion event へコピーする"""
    from dsp_engine.attribution import record_conversion
    from dsp_engine.bidder import record_dsp_win

    db.add(make_campaign(id="camp-v", creative_id="cre-v"))
    await db.commit()
    await record_dsp_win(
        db, campaign_id="camp-v", click_token="ct-v1", impression_id="i1",
        cleared_price_usd=20.0, bid_price_usd=22.0,
        bid_request=make_bid_request(publisher_id="pub-V", tagid="slot-V", country="JPN"),
    )

    event, created = await record_conversion(db, click_token="ct-v1", revenue_jpy=5000.0)
    assert created is True
    assert event.creative_id == "cre-v"
    assert event.publisher_id == "pub-V"
    assert event.placement == "slot-V"
    assert event.geo == "JPN"


# ── run_report: 新軸の集計 / 既存軸の非破壊 ────────────────────

@pytest.mark.asyncio
async def test_run_report_by_publisher(db):
    """publisher 軸で GROUP BY 集計できる"""
    from dsp_engine.reporting import run_report

    db.add(make_campaign(id="camp-r"))
    db.add(DspSpendLogDB(campaign_id="camp-r", click_token="t1",
                         publisher_id="pub-1", spend_jpy=100.0))
    db.add(DspSpendLogDB(campaign_id="camp-r", click_token="t2",
                         publisher_id="pub-1", spend_jpy=50.0))
    db.add(DspSpendLogDB(campaign_id="camp-r", click_token="t3",
                         publisher_id="pub-2", spend_jpy=30.0))
    await db.commit()

    rows = await run_report(db, date_from=date.today(), date_to=date.today(),
                            dimensions=["publisher"])
    by_pub = {r["publisher"]: r for r in rows}
    assert by_pub["pub-1"]["impressions"] == 2
    assert by_pub["pub-1"]["spend_jpy"] == 150.0
    assert by_pub["pub-2"]["impressions"] == 1


@pytest.mark.asyncio
async def test_run_report_campaign_dim_still_works(db):
    """既存の campaign 軸が #6 追加後も壊れていない（非破壊）"""
    from dsp_engine.reporting import run_report

    db.add(make_campaign(id="camp-old"))
    db.add(DspSpendLogDB(campaign_id="camp-old", click_token="o1", spend_jpy=200.0))
    await db.commit()

    rows = await run_report(db, date_from=date.today(), date_to=date.today(),
                            dimensions=["campaign"])
    assert rows[0]["campaign"] == "camp-old"
    assert rows[0]["spend_jpy"] == 200.0
