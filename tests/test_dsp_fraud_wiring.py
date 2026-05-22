"""
dsp_engine #8-2 fraud 監視エンドツーエンド配線の再現テスト (Red フェーズ)

検証対象:
  A: /click エンドポイント — レート制限時は DspClickEventDB を記録せず 302 継続
  B: /conversion エンドポイント — 異常 revenue を 0 に丸めて CV 自体は記録
  C: fraud.incr_click_counters — Redis (fakeredis) で INCR+EXPIRE が実カウントする
  D: bidder.handle_bid_request — brand safety ブロック + paced_out 混在で nbr=507

設計方針 (Planner 用メモ):
  - fraud.check_click_rate_limit は同期判定関数のまま保つ（_override_token_count/_override_ip_count）
  - 新規 async ヘルパー fraud.incr_click_counters(redis, token, ip) -> tuple[int,int] を追加
  - router.py の /click ハンドラが Request を受け取り、incr_click_counters で実カウント取得後
    check_click_rate_limit(_override_*=...) で判定する
  - nbr.py に NBR_IVT_DETECTED=506 / NBR_BRAND_SAFETY_BLOCK=507 を追加
  - bidder.handle_bid_request が brand_safety_blocked_count > 0 を優先チェック

実行: cd ssp_platform && pytest tests/test_dsp_fraud_wiring.py -v
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from db_models import DspBidLogDB, DspCampaignDB, DspClickEventDB, DspSpendLogDB


# ── フィクスチャ ────────────────────────────────────────────────


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


@pytest_asyncio.fixture
async def http_client(db):
    """インメモリ DB でオーバーライドした httpx.AsyncClient (ASGITransport)。"""
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
        yield c, session_factory
    app.dependency_overrides.clear()
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


def make_spend(campaign_id: str, click_token: str, spend_jpy: float = 0.1) -> DspSpendLogDB:
    return DspSpendLogDB(
        campaign_id=campaign_id,
        click_token=click_token,
        impression_id=None,
        platform="web",
        source="ssp-node",
        bid_price_jpy=0.0,
        cleared_price_jpy=0.0,
        spend_jpy=spend_jpy,
    )


def _imp(bidfloor: float = 0.0):
    from auction.openrtb import Banner, Impression
    return Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=bidfloor)


# ── A: /click エンドポイント (router.py 配線) ───────────────────


@pytest.mark.asyncio
async def test_click_normal_records_dsp_click_event(http_client):
    """/click の正常アクセスは DspClickEventDB を1件記録し 302 を返す"""
    client, session_factory = http_client
    # キャンペーン + 落札ログを用意
    async with session_factory() as session:
        camp = make_campaign(id="camp-click-ok")
        session.add(camp)
        await session.commit()
        session.add(make_spend("camp-click-ok", "ct-ok"))
        await session.commit()

    resp = await client.get("/dsp-engine/click", params={"ct": "ct-ok"}, follow_redirects=False)
    assert resp.status_code == 302

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(DspClickEventDB)
            .where(DspClickEventDB.click_token == "ct-ok")
        )
    assert count == 1, "正常クリックは DspClickEventDB を1件記録すること"


@pytest.mark.asyncio
async def test_click_rate_limited_no_dsp_click_event(http_client):
    """/click でレート制限超過時は DspClickEventDB を記録しないが 302 は継続する"""
    # 実装後: router.py が incr_click_counters で閾値超を検知し record_click をスキップする
    client, session_factory = http_client
    ct = "ct-ratelimit-" + uuid.uuid4().hex

    async with session_factory() as session:
        session.add(make_campaign(id="camp-rl"))
        await session.commit()
        session.add(make_spend("camp-rl", ct))
        await session.commit()

    # ここでは router.py の rate_limit 分岐をテストするため、
    # monkeypatch で incr_click_counters が超過カウントを返すようにする
    import dsp_engine.router as router_mod

    async def _fake_incr(redis, token, ip):
        # token カウント 999 (閾値超)、ip カウント 1
        return (999, 1)

    # 実装前なので incr_click_counters 自体が存在せず ImportError になるはずだが、
    # テストとして正しい挙動を先に記述する (Red)
    import importlib
    import dsp_engine.fraud as fraud_mod  # 存在しなければ ImportError -> Red

    original = getattr(fraud_mod, "incr_click_counters", None)
    fraud_mod.incr_click_counters = _fake_incr

    try:
        resp = await client.get("/dsp-engine/click", params={"ct": ct}, follow_redirects=False)
        # レート制限超過でも 302 リダイレクトは継続
        assert resp.status_code == 302, "レート制限超過でも 302 を返すこと"

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(DspClickEventDB)
                .where(DspClickEventDB.click_token == ct)
            )
        assert count == 0, "レート制限超過時は DspClickEventDB を記録しないこと"
    finally:
        if original is not None:
            fraud_mod.incr_click_counters = original
        else:
            if hasattr(fraud_mod, "incr_click_counters"):
                delattr(fraud_mod, "incr_click_counters")


# ── B: /conversion エンドポイント (revenue_jpy ガード) ─────────


@pytest.mark.asyncio
async def test_conversion_normal_revenue_recorded(http_client):
    """/conversion に正常な revenue_jpy を送ると CV イベントが記録される"""
    from db_models import DspConversionEventDB
    client, session_factory = http_client

    async with session_factory() as session:
        session.add(make_campaign(id="camp-cv-ok"))
        await session.commit()

    resp = await client.get(
        "/dsp-engine/conversion",
        params={"campaign_id": "camp-cv-ok", "revenue_jpy": "5000", "dedup_key": "cv-ok-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] is True

    async with session_factory() as session:
        event = await session.scalar(
            select(DspConversionEventDB)
            .where(DspConversionEventDB.dedup_key == "cv-ok-1")
        )
    assert event is not None
    assert abs(event.revenue_jpy - 5000.0) < 1e-6, "正常 revenue は記録されること"


@pytest.mark.asyncio
async def test_conversion_negative_revenue_zeroed(http_client):
    """/conversion に負の revenue_jpy を送ると revenue_jpy=0 で CV イベントが記録される"""
    from db_models import DspConversionEventDB
    client, session_factory = http_client

    async with session_factory() as session:
        session.add(make_campaign(id="camp-cv-neg"))
        await session.commit()

    resp = await client.get(
        "/dsp-engine/conversion",
        params={"campaign_id": "camp-cv-neg", "revenue_jpy": "-1000", "dedup_key": "cv-neg-1"},
    )
    # 実装後: 200 応答、CV は記録されるが revenue_jpy=0 に丸められる
    assert resp.status_code == 200, "負の revenue でも 200 を返すこと"
    data = resp.json()
    assert data["created"] is True, "CV イベント自体は記録されること"

    async with session_factory() as session:
        event = await session.scalar(
            select(DspConversionEventDB)
            .where(DspConversionEventDB.dedup_key == "cv-neg-1")
        )
    assert event is not None
    assert event.revenue_jpy == 0.0, "負の revenue_jpy は 0 に丸められること"


@pytest.mark.asyncio
async def test_conversion_outlier_revenue_zeroed(http_client):
    """/conversion に異常に大きい revenue_jpy を送ると revenue_jpy=0 で記録される"""
    from db_models import DspConversionEventDB
    client, session_factory = http_client

    async with session_factory() as session:
        session.add(make_campaign(id="camp-cv-big"))
        await session.commit()

    # デフォルト上限倍率: avg_purchase_value_jpy(3000) * 上限倍率(例: 1000) = 3,000,000 超
    outlier_revenue = 99_999_999.0
    resp = await client.get(
        "/dsp-engine/conversion",
        params={
            "campaign_id": "camp-cv-big",
            "revenue_jpy": str(outlier_revenue),
            "dedup_key": "cv-big-1",
        },
    )
    assert resp.status_code == 200, "外れ値 revenue でも 200 を返すこと"
    data = resp.json()
    assert data["created"] is True, "CV イベント自体は記録されること"

    async with session_factory() as session:
        event = await session.scalar(
            select(DspConversionEventDB)
            .where(DspConversionEventDB.dedup_key == "cv-big-1")
        )
    assert event is not None
    assert event.revenue_jpy == 0.0, "外れ値 revenue_jpy は 0 に丸められること"


# ── C: fraud.incr_click_counters — 実 Redis (fakeredis) テスト ─


@pytest.mark.asyncio
async def test_incr_click_counters_accumulates_within_window():
    """incr_click_counters は同一 token/ip でウィンドウ内に複数回呼ぶと累積カウントを返す"""
    # 実装前は dsp_engine.fraud に incr_click_counters が存在しないため ImportError -> Red
    from dsp_engine.fraud import incr_click_counters  # noqa: F401 — ImportError で Red

    try:
        import fakeredis.aioredis as fakeredis_aio
        redis = fakeredis_aio.FakeRedis(decode_responses=True)
    except ImportError:
        # fakeredis 未インストールの場合は最小 stub を使う
        class _FakeRedis:
            _store: dict = {}

            async def incr(self, key: str) -> int:
                self._store[key] = self._store.get(key, 0) + 1
                return self._store[key]

            async def expire(self, key: str, ttl: int) -> None:
                pass

        redis = _FakeRedis()

    token = "tok-c-" + uuid.uuid4().hex
    ip = "192.168.1.1"

    c1_t, c1_i = await incr_click_counters(redis, token, ip)
    c2_t, c2_i = await incr_click_counters(redis, token, ip)
    c3_t, c3_i = await incr_click_counters(redis, token, ip)

    assert c1_t == 1, "1回目の token カウントは 1 であること"
    assert c2_t == 2, "2回目の token カウントは 2 に累積すること"
    assert c3_t == 3, "3回目の token カウントは 3 に累積すること"
    assert c1_i == 1 and c2_i == 2, "ip カウントも独立して累積すること"


@pytest.mark.asyncio
async def test_check_click_rate_limit_triggers_at_threshold():
    """incr_click_counters で累積したカウントが閾値を超えると check_click_rate_limit が True を返す。

    #8-2 のスコープ: incr_click_counters (実 Redis カウンタ) が返した token/ip カウントを
    check_click_rate_limit の _override_*_count に渡すことで end-to-end の判定ができることを検証する。
    incr_click_counters は #8-2 実装前には存在しないため ImportError で Red となる。
    """
    # incr_click_counters は #8-2 実装前は未存在 -> ImportError で Red
    from dsp_engine.fraud import incr_click_counters  # noqa: F401 — ImportError で Red
    from dsp_engine.fraud import check_click_rate_limit

    token_limit = 3
    ip_limit = 5
    window_seconds = 3600

    class _FakeRedis:
        """最小インメモリ Redis スタブ（fakeredis 不要の軽量版）"""
        _store: dict = {}

        async def incr(self, key: str) -> int:
            self._store[key] = self._store.get(key, 0) + 1
            return self._store[key]

        async def expire(self, key: str, ttl: int) -> None:
            pass

    redis = _FakeRedis()
    token = "tok-thresh-" + uuid.uuid4().hex
    ip = "10.0.0.99"

    # token_limit=3 に達するまで閾値未満 -> False
    for _ in range(token_limit):
        t_count, i_count = await incr_click_counters(redis, token, ip)
    # t_count == token_limit（== 境界値）、閾値判定は count > limit なので False
    assert check_click_rate_limit(
        None, token, ip,
        token_limit=token_limit, ip_limit=ip_limit, window_seconds=window_seconds,
        _override_token_count=t_count, _override_ip_count=i_count,
    ) is False, "閾値ちょうどは制限なし(False)"

    # もう1回 incr して閾値超過
    t_over, i_over = await incr_click_counters(redis, token, ip)
    assert check_click_rate_limit(
        None, token, ip,
        token_limit=token_limit, ip_limit=ip_limit, window_seconds=window_seconds,
        _override_token_count=t_over, _override_ip_count=i_over,
    ) is True, "token カウントが閾値を超えたら True"


# ── D: bidder.handle_bid_request — brand safety 優先発火 ────────


@pytest.mark.asyncio
async def test_brand_safety_blocked_wins_over_paced_out(db):
    """brand_safety_blocked_count > 0 かつ paced_out 混在時に NBR_BRAND_SAFETY_BLOCK(507) が優先記録される。

    現状 (LOW-2 未是正): bidder.py は `brand_safety_blocked_count > 0 and paced_out_count == 0`
    の場合のみ 507 を発火し、paced_out が混在すると NBR_ALL_BUDGET_PACED(501) になる。
    LOW-2 是正後: brand_safety_blocked_count > 0 であれば paced_out 混在でも 507 を優先発火する。
    このテストが Red である理由: 現状の条件では 501 が記録され、507 のログが存在しないため
    assert log is not None が失敗する。
    """
    from dsp_engine.nbr import NBR_BRAND_SAFETY_BLOCK

    from auction.openrtb import BidRequest, Site
    from dsp_engine.bidder import handle_bid_request

    # camp-bs: bcat_block="IAB25" で brand safety ブロックを発火させる
    db.add(make_campaign(
        id="camp-bs",
        base_ctr=0.1,
        target_cvr=0.1,
        avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0,
        bid_cap_jpy=100_000.0,
        bcat_block='["IAB25"]',
        badv_block="[]",
    ))
    # camp-paced-bs: 予算超過で paced_out になるキャンペーン（混在ケース）
    db.add(make_campaign(
        id="camp-paced-bs",
        base_ctr=0.1,
        target_cvr=0.1,
        avg_purchase_value_jpy=10_000.0,
        bid_floor_jpy=100.0,
        bid_cap_jpy=100_000.0,
        total_budget_jpy=1.0,
    ))
    db.add(make_spend("camp-paced-bs", "tok-paced-bs", spend_jpy=100.0))
    await db.commit()

    # IAB25-3 を持つ Site -> camp-bs の bcat_block=["IAB25"] と一致してブロックされる
    req = BidRequest(
        imp=[_imp()],
        site=Site(cat=["IAB25-3"], page="https://example.com/adult"),
    )
    resp = await handle_bid_request(req, db, source="test")

    # LOW-2 是正後: brand_safety_blocked_count > 0 なら 507 が優先 -> resp=None かつ nbr=507
    # LOW-2 未是正 (現状): paced_out 混在で 501 が記録 -> nbr=507 ログが存在せず assert 失敗 (Red)
    log = await db.scalar(
        select(DspBidLogDB)
        .where(DspBidLogDB.nbr == NBR_BRAND_SAFETY_BLOCK)
    )
    assert log is not None, (
        "brand_safety_blocked_count > 0 かつ paced_out 混在時に "
        "NBR_BRAND_SAFETY_BLOCK(507) が DspBidLogDB に記録されること"
    )
    assert log.outcome == "no_bid"
