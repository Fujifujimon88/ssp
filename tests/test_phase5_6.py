"""Phase 5（アフィリエイト管理・計測）/ Phase 6（ダッシュボード）テスト"""
import pytest
import pytest_asyncio

from tests.conftest import *  # noqa: F401, F403 — client, admin_key fixtures


# ── Phase 5: GTM LP ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lp_campaign_not_found(client):
    resp = await client.get("/mdm/lp/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lp_with_active_campaign(client, admin_key):
    # 案件を作成
    r = await client.post(
        "/mdm/admin/affiliate/campaigns",
        json={
            "name": "NordVPN テスト",
            "category": "vpn",
            "destination_url": "https://nordvpn.com/",
            "reward_type": "cps",
            "reward_amount": 2000.0,
            "gtm_container_id": "GTM-TEST123",
        },
        headers={"x-admin-key": admin_key},
    )
    assert r.status_code == 200
    campaign_id = r.json()["id"]

    # LPにアクセス → GTMスニペット付きHTMLが返る
    resp = await client.get(f"/mdm/lp/{campaign_id}")
    assert resp.status_code == 200
    html = resp.text
    assert "GTM-TEST123" in html
    assert "googletagmanager.com" in html
    assert "NordVPN テスト" in html

    return campaign_id


@pytest.mark.asyncio
async def test_lp_without_gtm(client, admin_key):
    r = await client.post(
        "/mdm/admin/affiliate/campaigns",
        json={
            "name": "GTMなし案件",
            "category": "app",
            "destination_url": "https://example.com/",
            "reward_type": "cpi",
            "reward_amount": 300.0,
        },
        headers={"x-admin-key": admin_key},
    )
    campaign_id = r.json()["id"]
    resp = await client.get(f"/mdm/lp/{campaign_id}")
    assert resp.status_code == 200
    assert "googletagmanager.com" not in resp.text


# ── Phase 5: 精算レポート ──────────────────────────────────────

@pytest.mark.asyncio
async def test_monthly_revenue_report_empty(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/report",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "period" in data
    assert "total_revenue_jpy" in data
    assert "total_conversions" in data
    assert isinstance(data["by_campaign"], list)


@pytest.mark.asyncio
async def test_monthly_revenue_report_with_params(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/report?year=2026&month=3",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    assert resp.json()["period"] == "2026-03"


@pytest.mark.asyncio
async def test_all_dealers_report(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/report-all",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_dealer_report_not_found(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/report/nonexistent-dealer",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_conversions_list_empty(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/conversions",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_conversions_filter_by_source(client, admin_key):
    resp = await client.get(
        "/mdm/admin/affiliate/conversions?source=appsflyer&limit=10",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200


# ── Phase 6: ダッシュボード ────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_dashboard_returns_html(client, admin_key):
    resp = await client.get(
        "/mdm/admin/dashboard",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "MDM管理ダッシュボード" in html
    assert "今月収益" in html
    assert "代理店 Top 5" in html
    assert "アフィリエイト案件 Top 5" in html


@pytest.mark.asyncio
async def test_admin_dashboard_without_key_returns_401(client):
    resp = await client.get("/mdm/admin/dashboard")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dealer_portal_invalid_key(client):
    resp = await client.get("/mdm/dealer/portal?api_key=wrong-key")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dealer_portal_valid_key(client, admin_key):
    # 代理店を作成してAPIキーを取得
    r = await client.post(
        "/mdm/admin/dealers",
        json={"name": "テスト店舗", "store_code": "TEST-P6-001"},
        headers={"x-admin-key": admin_key},
    )
    assert r.status_code == 200
    dealer_api_key = r.json()["api_key"]

    resp = await client.get(f"/mdm/dealer/portal?api_key={dealer_api_key}")
    assert resp.status_code == 200
    html = resp.text
    assert "テスト店舗" in html
    assert "エンロール端末数" in html
    assert "今月収益" in html


@pytest.mark.asyncio
async def test_advertiser_portal_not_found(client, admin_key):
    resp = await client.get(
        "/mdm/advertiser/portal/nonexistent",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_advertiser_portal_valid(client, admin_key):
    r = await client.post(
        "/mdm/admin/affiliate/campaigns",
        json={
            "name": "広告主テスト案件",
            "category": "app",
            "destination_url": "https://example.com/",
            "reward_type": "cpi",
            "reward_amount": 500.0,
            "gtm_container_id": "GTM-ADVTEST",
            "appsflyer_dev_key": "test-af-key",
        },
        headers={"x-admin-key": admin_key},
    )
    campaign_id = r.json()["id"]

    resp = await client.get(
        f"/mdm/advertiser/portal/{campaign_id}",
        headers={"x-admin-key": admin_key},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "広告主テスト案件" in html
    assert "GTM-ADVTEST" in html
    assert "設定済み" in html   # AppsFlyer
    assert "CV履歴" in html
