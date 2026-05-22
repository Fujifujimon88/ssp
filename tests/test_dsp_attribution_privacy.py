"""
dsp_engine #9 — MMP 署名検証 / PII サニタイズ / アトリビューション窓
Red フェーズ: 失敗するテストのみを先行実装。production code は変更しない。

カバー範囲:
  A: /conversion ポストバック HMAC-SHA256 署名検証
  B: raw_payload 保存前の PII キーサニタイズ
  C: アトリビューション窓 (lookback window) による campaign 紐付けスキップ

実行: python -m pytest tests/test_dsp_attribution_privacy.py -v
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from db_models import DspCampaignDB, DspConversionEventDB, DspSpendLogDB
from main import app

# ── ヘルパー ───────────────────────────────────────────────────────────────


def make_campaign(**kw) -> DspCampaignDB:
    """テスト用 DspCampaignDB（id を必ず指定すること）"""
    defaults = dict(
        id="camp-priv-1",
        advertiser_name="プライバシーテスト広告主",
        campaign_name="プライバシーテストキャンペーン",
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
        creative_title="テスト広告",
        creative_click_url="https://advertiser.example.com/lp",
    )
    defaults.update(kw)
    return DspCampaignDB(**defaults)


def make_spend_log(
    campaign_id: str,
    click_token: str,
    logged_at: datetime,
    impression_id: str = "imp-test-001",
) -> DspSpendLogDB:
    """テスト用 DspSpendLogDB。logged_at を任意の時刻に設定できる。

    impression_id を明示的に設定することで、窓判定テストで
    「impression_id が紐付いたか否か」を確認できる。
    """
    return DspSpendLogDB(
        campaign_id=campaign_id,
        click_token=click_token,
        impression_id=impression_id,
        platform="android",
        source="ssp-node",
        bid_price_jpy=500.0,
        cleared_price_jpy=400.0,
        spend_jpy=0.4,
        logged_at=logged_at,
    )


def _sign_postback(secret: str, click_token: str, revenue: str, dedup_key: str) -> str:
    """#9 想定 canonical: click_token|revenue_jpy|dedup_key の HMAC-SHA256。"""
    canonical = f"{click_token}|{revenue}|{dedup_key}"
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _make_mock_settings(**overrides) -> SimpleNamespace:
    """テスト用 settings 相当のオブジェクト。実 settings の全フィールドをコピーし上書きする。

    SimpleNamespace を使うことで Pydantic の frozen/field 制約を回避する。
    """
    ns = SimpleNamespace(
        # DB / Redis
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        # JWT
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        access_token_expire_minutes=settings.access_token_expire_minutes,
        # オークション
        auction_timeout_ms=settings.auction_timeout_ms,
        floor_price_default=settings.floor_price_default,
        # dsp_engine
        jpy_per_usd=settings.jpy_per_usd,
        warm_threshold=settings.warm_threshold,
        # 管理者
        admin_api_key=settings.admin_api_key,
        basic_auth_user=settings.basic_auth_user,
        basic_auth_password=settings.basic_auth_password,
        admin_allowed_ips=settings.admin_allowed_ips,
        proxy_trusted_hosts=settings.proxy_trusted_hosts,
        # アプリ
        app_env=settings.app_env,
        ssp_endpoint=settings.ssp_endpoint,
        revenue_share_rate=settings.revenue_share_rate,
        ssp_domain=settings.ssp_domain,
        ssp_seller_id=settings.ssp_seller_id,
        ssp_contact_email=settings.ssp_contact_email,
        # LINE / eru-nage / FCM / MDM
        line_channel_access_token=settings.line_channel_access_token,
        line_channel_secret=settings.line_channel_secret,
        line_official_account_id=settings.line_official_account_id,
        eru_nage_api_url=settings.eru_nage_api_url,
        eru_nage_api_key=settings.eru_nage_api_key,
        fcm_project_id=settings.fcm_project_id,
        fcm_service_account_path=settings.fcm_service_account_path,
        nanomdm_url=settings.nanomdm_url,
        nanomdm_api_key=settings.nanomdm_api_key,
        apns_cert_path=settings.apns_cert_path,
        apns_key_path=settings.apns_key_path,
        apns_topic=settings.apns_topic,
        apns_production=settings.apns_production,
        mdm_server_url=settings.mdm_server_url,
        mdm_push_magic=settings.mdm_push_magic,
        app_bundle_id=settings.app_bundle_id,
        # dsp_engine: fraud / IVT
        asp_postback_secret=settings.asp_postback_secret,
        dsp_ivt_strict=settings.dsp_ivt_strict,
        dsp_datacenter_cidrs=settings.dsp_datacenter_cidrs,
        dsp_click_token_limit=settings.dsp_click_token_limit,
        dsp_click_ip_limit=settings.dsp_click_ip_limit,
        dsp_click_window_seconds=settings.dsp_click_window_seconds,
        dsp_revenue_cap_multiplier=settings.dsp_revenue_cap_multiplier,
        # #9 新設定（Green 段で config.py に追加。Red 段ではデフォルト値を持つ）
        dsp_postback_hmac_secret="",
        dsp_attribution_window_days=30,
        dsp_pii_strip_keys="idfa,gaid,device_id,ip,user_agent,ua,android_id,appsflyer_id",
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


# ── フィクスチャ ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def priv_client():
    """隔離インメモリ DB + FastAPI クライアント（win_client と同パターン）。

    conftest の module-scoped client は file DB を使い Windows でロック競合するため、
    :memory: + StaticPool でこのモジュール内に完全隔離する。
    """
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
        yield c, session_factory  # セッションも yield してアサートに使う
    app.dependency_overrides.clear()
    await engine.dispose()


def _patch_settings(mock_settings: SimpleNamespace):
    """router モジュールの settings を一括パッチするコンテキストマネージャ群を返す。

    attribution.py と fraud.py は現状 settings を直接 import していないため除外。
    #9 Green 段で attribution.py が settings を import したら attr_module のパッチを追加する。
    """
    import dsp_engine.router as router_module
    import config as config_module

    return [
        patch.object(router_module, "settings", mock_settings),
        patch.object(config_module, "settings", mock_settings),
    ]


# ── スコープ A: HMAC 署名検証 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conversion_valid_hmac_signature_returns_200(priv_client):
    """A-1: 正しい HMAC-SHA256 署名付きポストバックは 200 を返す。

    Red 段: dsp_postback_hmac_secret を参照して HMAC 検証するロジックが
    router.py に未実装のため、現状は signature を無視して通過してしまうか、
    または 200 を返す（この場合 Green になってしまうが A-2 が Red で補完する）。
    確実に Red にするため、正しい署名のリクエストが 200 になることを確認する
    （A-2 で不正署名が 401 になることを確認する）。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-sig-ok"))
        await db.commit()

    secret = "test-hmac-secret-for-postback"
    click_token = "ct-hmac-valid"
    revenue = "5000.0"
    dedup_key = "dedup-hmac-1"
    sig = _sign_postback(secret, click_token, revenue, dedup_key)

    mock_settings = _make_mock_settings(
        dsp_postback_hmac_secret=secret,
        asp_postback_secret="",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-sig-ok",
                "click_token": click_token,
                "revenue_jpy": float(revenue),
                "dedup_key": dedup_key,
                "signature": sig,
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_conversion_invalid_hmac_signature_returns_401(priv_client):
    """A-2: 不正な HMAC-SHA256 署名付きポストバックは 401 を返す。

    Red 段: HMAC 検証ロジックが未実装のため signature を無視して 200 を返してしまう。
    → このテストは Red (200 != 401) になる。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-sig-bad"))
        await db.commit()

    secret = "test-hmac-secret-for-postback"
    mock_settings = _make_mock_settings(
        dsp_postback_hmac_secret=secret,
        asp_postback_secret="",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-sig-bad",
                "revenue_jpy": 5000.0,
                "dedup_key": "dedup-hmac-bad",
                "signature": "deadbeefdeadbeefdeadbeefdeadbeef",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_conversion_no_signature_with_static_secret_match_returns_200(priv_client):
    """A-3: signature パラメータなし + 静的 asp_postback_secret 一致 → 200 (後方互換)。

    Red 段: verify_postback_secret が未実装だが現状の != 比較でも 200 を返す。
    このテストは現状 pass する可能性があるが A-2 / A-4 / A-5 とバンドルで Red。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-static-ok"))
        await db.commit()

    static_secret = "static-postback-secret"
    mock_settings = _make_mock_settings(
        asp_postback_secret=static_secret,
        dsp_postback_hmac_secret="",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-static-ok",
                "revenue_jpy": 1000.0,
                "dedup_key": "dedup-static-1",
                "secret": static_secret,
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_conversion_static_secret_mismatch_returns_401(priv_client):
    """A-4: 静的 asp_postback_secret 不一致 → 401。timing-safe 比較に切り替えても挙動は同じ。

    Red 段: このテストは現状 pass する可能性があるが A-2 / A-5 とバンドルで Red。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-static-bad"))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="correct-secret",
        dsp_postback_hmac_secret="",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-static-bad",
                "revenue_jpy": 1000.0,
                "dedup_key": "dedup-static-bad",
                "secret": "wrong-secret",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_conversion_timing_safe_comparison_used():
    """A-5: 静的シークレット比較は hmac.compare_digest (timing-safe) を使うこと。

    Red 段: verify_postback_secret 関数が attribution モジュールに存在しないので
    ImportError で失敗する。
    """
    # #9 実装後: dsp_engine.attribution に verify_postback_secret が追加される。
    from dsp_engine.attribution import verify_postback_secret  # noqa: F401 — Red: ImportError expected

    assert callable(verify_postback_secret), "verify_postback_secret must be callable"
    # timing-safe であることを確認: 正しいシークレットは True
    assert verify_postback_secret("secret", "secret") is True
    # 不正なシークレットは False
    assert verify_postback_secret("wrong", "correct") is False


# ── スコープ B: PII サニタイズ ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pii_keys_removed_from_raw_payload(priv_client):
    """B-1: raw_payload に PII キーを含むポストバックを送ると保存時に PII が除去される。

    Red 段: router.py がサニタイズせずに raw_payload を保存するため、
    保存後の raw_payload に PII キーが残ってしまい assertion fail になる。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-pii-strip"))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="",
        dsp_postback_hmac_secret="",
        dsp_pii_strip_keys="idfa,gaid,device_id,ip,user_agent,ua,android_id,appsflyer_id",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-pii-strip",
                "revenue_jpy": 3000.0,
                "dedup_key": "dedup-pii-1",
                # PII キー
                "idfa": "00000000-0000-0000-0000-000000000001",
                "gaid": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
                "device_id": "device-xyz",
                "ip": "192.168.1.1",
                "user_agent": "Mozilla/5.0 (iPhone)",
                "android_id": "android-xyz",
                "appsflyer_id": "appsflyer-xyz",
                # 非 PII キー（残るべき）
                "event_type": "purchase",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # DB に保存された raw_payload に PII が残っていないことを確認
    async with session_factory() as db:
        event = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == "dedup-pii-1")
        )
    assert event is not None, "conversion event should be saved"
    assert event.raw_payload is not None

    pii_keys = ["idfa", "gaid", "device_id", "ip", "user_agent", "android_id", "appsflyer_id"]
    for key in pii_keys:
        assert f"'{key}'" not in event.raw_payload and f'"{key}"' not in event.raw_payload, \
            f"PII key '{key}' should have been stripped from raw_payload"


@pytest.mark.asyncio
async def test_non_pii_keys_retained_in_raw_payload(priv_client):
    """B-2: 非 PII キー (event_type, revenue_jpy 等) は raw_payload に残る。

    Red 段: サニタイズ未実装の場合でも通る可能性あり。B-1 とバンドルで Red。
    """
    client, session_factory = priv_client
    async with session_factory() as db:
        db.add(make_campaign(id="camp-pii-retain"))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="",
        dsp_postback_hmac_secret="",
        dsp_pii_strip_keys="idfa,gaid,device_id,ip,user_agent,ua,android_id,appsflyer_id",
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "campaign_id": "camp-pii-retain",
                "revenue_jpy": 2000.0,
                "dedup_key": "dedup-pii-retain",
                "event_type": "purchase",
                "idfa": "should-be-removed",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200

    async with session_factory() as db:
        event = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == "dedup-pii-retain")
        )
    assert event is not None
    # event_type か revenue_jpy が raw_payload に含まれていること
    assert "event_type" in event.raw_payload or "revenue_jpy" in event.raw_payload, \
        "Non-PII keys should remain in raw_payload"


def test_sanitize_pii_function_exists_and_works():
    """B-3: sanitize_pii_payload 関数が attribution モジュールに存在し、PII を除去する。

    Red 段: 関数未実装のため ImportError で失敗する。
    """
    from dsp_engine.attribution import sanitize_pii_payload  # noqa: F401 — Red: ImportError expected

    pii_payload = {
        "campaign_id": "camp-1",
        "revenue_jpy": 5000.0,
        "idfa": "secret-idfa",
        "gaid": "secret-gaid",
        "ip": "10.0.0.1",
        "user_agent": "Mozilla/5.0",
        "event_type": "purchase",
    }
    pii_keys = ["idfa", "gaid", "ip", "user_agent"]
    sanitized = sanitize_pii_payload(pii_payload, pii_keys=pii_keys)

    # PII キーが除去されている
    assert "idfa" not in sanitized
    assert "gaid" not in sanitized
    assert "ip" not in sanitized
    assert "user_agent" not in sanitized
    # 非 PII は残っている
    assert sanitized.get("campaign_id") == "camp-1"
    assert sanitized.get("event_type") == "purchase"


def test_sanitize_does_not_break_dedup_and_revenue_normalization():
    """B-4: サニタイズ後も dedup_key / revenue の正規化が機能する。

    Red 段: sanitize_pii_payload 未実装のため ImportError で失敗する。
    """
    from dsp_engine.attribution import normalize_conversion_payload, sanitize_pii_payload

    raw = {
        "campaign_id": "camp-1",
        "af_revenue": "3000",
        "af_event_id": "evt-999",
        "event_revenue_currency": "JPY",
        "af_event_name": "af_purchase",
        "idfa": "should-be-removed",
        "appsflyer_id": "appsflyer-xyz",
    }
    pii_keys = ["idfa", "appsflyer_id"]
    sanitized = sanitize_pii_payload(raw, pii_keys=pii_keys)
    norm = normalize_conversion_payload(sanitized)

    assert norm["revenue_jpy"] == 3000.0
    assert norm["dedup_key"] == "evt-999"
    assert norm["event_type"] == "af_purchase"
    assert "idfa" not in sanitized
    assert "appsflyer_id" not in sanitized


# ── スコープ C: アトリビューション窓 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_conversion_within_window_is_attributed_to_campaign(priv_client):
    """C-1: 窓内 (logged_at が最近) のクリックの CV は campaign に紐付く。

    Red 段: record_conversion に窓判定ロジックが未実装でも現状は campaign に紐付くため
    このテスト単体は pass するが、C-2 とバンドルで Red になる。
    """
    client, session_factory = priv_client
    now_utc = datetime.now(timezone.utc)
    recent_logged_at = now_utc - timedelta(days=5)  # 窓内（デフォルト30日以内）

    async with session_factory() as db:
        db.add(make_campaign(id="camp-win-in"))
        db.add(make_spend_log("camp-win-in", "ct-win-in", logged_at=recent_logged_at))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="",
        dsp_postback_hmac_secret="",
        dsp_attribution_window_days=30,
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "click_token": "ct-win-in",
                "revenue_jpy": 4000.0,
                "dedup_key": "dedup-win-in",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200

    async with session_factory() as db:
        event = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == "dedup-win-in")
        )
    assert event is not None
    assert event.campaign_id == "camp-win-in", "窓内 CV は campaign に紐付くべき"
    assert event.click_token == "ct-win-in", "窓内 CV は click_token が保存されるべき"


@pytest.mark.asyncio
async def test_conversion_outside_window_is_recorded_but_not_attributed(priv_client):
    """C-2: 窓外 (logged_at が古い) のクリックの CV は記録されるが impression_id 未紐付け。

    Red 段: record_conversion に窓判定がないため現状は impression_id が紐付いてしまう。
    → このテストは Red (assertion fail) になる。
    """
    client, session_factory = priv_client
    now_utc = datetime.now(timezone.utc)
    old_logged_at = now_utc - timedelta(days=45)  # 窓外（デフォルト30日超）

    async with session_factory() as db:
        db.add(make_campaign(id="camp-win-out"))
        db.add(make_spend_log("camp-win-out", "ct-win-out", logged_at=old_logged_at))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="",
        dsp_postback_hmac_secret="",
        dsp_attribution_window_days=30,
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "click_token": "ct-win-out",
                "revenue_jpy": 4000.0,
                "dedup_key": "dedup-win-out",
            },
        )
    finally:
        for p in patches:
            p.stop()
    # CV は記録される（200）
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async with session_factory() as db:
        event = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == "dedup-win-out")
        )
    # CV は保存される（未アトリビュートでも記録は必須）
    assert event is not None, "窓外 CV も DB に記録されるべき"
    # 窓外なので spend_log 由来の impression_id は紐付かないべき
    assert event.impression_id is None, \
        "窓外 CV は impression_id が紐付かないべき（未アトリビュート）"
    # Fix Iteration 1: attributed=False で ROAS 非算入フラグが立っていること
    assert event.attributed is False, \
        "窓外 CV は attributed=False であるべき（ROAS集計から除外）"


@pytest.mark.asyncio
async def test_conversion_at_window_boundary_is_attributed(priv_client):
    """C-3: 窓境界値 (logged_at = 今から丁度 window_days 日前) は窓内扱い → impression_id 紐付く。

    Red 段: record_conversion に窓判定がないため現状は impression_id が紐付く（通過する）。
    C-2 が fail することで Red バンドル全体は失敗となる。
    """
    client, session_factory = priv_client
    now_utc = datetime.now(timezone.utc)
    boundary_logged_at = now_utc - timedelta(days=30)  # ちょうど窓境界

    async with session_factory() as db:
        db.add(make_campaign(id="camp-win-boundary"))
        db.add(make_spend_log("camp-win-boundary", "ct-win-boundary", logged_at=boundary_logged_at))
        await db.commit()

    mock_settings = _make_mock_settings(
        asp_postback_secret="",
        dsp_postback_hmac_secret="",
        dsp_attribution_window_days=30,
    )
    patches = _patch_settings(mock_settings)
    for p in patches:
        p.start()
    try:
        resp = await client.post(
            "/dsp-engine/conversion",
            json={
                "click_token": "ct-win-boundary",
                "revenue_jpy": 2500.0,
                "dedup_key": "dedup-win-boundary",
            },
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200

    async with session_factory() as db:
        event = await db.scalar(
            select(DspConversionEventDB).where(DspConversionEventDB.dedup_key == "dedup-win-boundary")
        )
    assert event is not None
    assert event.campaign_id == "camp-win-boundary", "境界値（丁度 window_days）は窓内扱いで紐付くべき"


# ── Fix Iteration 1: attributed フィルタによる ROAS 集計除外検証 ─────────────────


@pytest.mark.asyncio
async def test_outside_window_cv_excluded_from_roas_stats(priv_client):
    """D-1: 窓外 CV は get_campaign_stats / get_campaign_roas の集計に算入されない。

    Fix Iteration 1 (Reviewer HIGH 対応):
    窓外 CV を記録後、get_campaign_stats の conversions/revenue_jpy が 0 のままであること、
    窓内 CV では正しく算入されることを対比で確認する。
    """
    from dsp_engine.attribution import get_campaign_roas, record_conversion

    client, session_factory = priv_client
    now_utc = datetime.now(timezone.utc)

    async with session_factory() as db:
        db.add(make_campaign(id="camp-roas-test"))
        # 窓内 spend log
        db.add(make_spend_log("camp-roas-test", "ct-roas-in", logged_at=now_utc - timedelta(days=5)))
        # 窓外 spend log
        db.add(make_spend_log("camp-roas-test", "ct-roas-out", logged_at=now_utc - timedelta(days=45)))
        await db.commit()

    async with session_factory() as db:
        # 窓外 CV を記録
        event_out, created_out = await record_conversion(
            db,
            click_token="ct-roas-out",
            revenue_jpy=10000.0,
            dedup_key="dedup-roas-out",
            window_days=30,
        )
        assert created_out is True
        assert event_out.attributed is False, "窓外 CV は attributed=False"

    async with session_factory() as db:
        # 窓外 CV のみ存在する段階: conversions=0, revenue_jpy=0.0
        from dsp_engine.campaign_manager import get_campaign_stats
        stats_after_out = await get_campaign_stats(db, "camp-roas-test")
    assert stats_after_out["conversions"] == 0, \
        "窓外 CV は conversions に算入されないべき"
    assert stats_after_out["revenue_jpy"] == 0.0, \
        "窓外 CV は revenue_jpy に算入されないべき"

    async with session_factory() as db:
        # 窓内 CV を記録
        event_in, created_in = await record_conversion(
            db,
            click_token="ct-roas-in",
            revenue_jpy=5000.0,
            dedup_key="dedup-roas-in",
            window_days=30,
        )
        assert created_in is True
        assert event_in.attributed is True, "窓内 CV は attributed=True"

    async with session_factory() as db:
        # 窓内 CV 記録後: conversions=1, revenue_jpy=5000
        stats_after_in = await get_campaign_stats(db, "camp-roas-test")
    assert stats_after_in["conversions"] == 1, \
        "窓内 CV は conversions に算入されるべき"
    assert stats_after_in["revenue_jpy"] == 5000.0, \
        "窓内 CV は revenue_jpy に算入されるべき"
