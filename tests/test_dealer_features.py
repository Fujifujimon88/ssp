"""ディーラー向け機能テスト（8件）

テスト対象:
  GET /mdm/dealer/stats/today
  POST /mdm/dealer/push
  GET /mdm/dealer/webclips
  PUT /mdm/dealer/webclips
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from db_models import AndroidDeviceDB, CampaignDB, DealerDB, DealerPushLogDB, DeviceDB


# ── フィクスチャ ────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def dealer(client):
    """テスト用ディーラーを作成して返す"""
    from config import settings
    headers = {"X-Admin-Key": settings.admin_api_key}
    resp = await client.post(
        "/mdm/admin/dealers",
        json={
            "name": "テスト店舗",
            "store_code": "TEST-DEALER-001",
            "address": "東京都渋谷区1-1-1",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── stats/today ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dealer_stats_today_ok(client, dealer):
    api_key = dealer["api_key"]
    resp = await client.get(f"/mdm/dealer/stats/today?api_key={api_key}")
    assert resp.status_code == 200
    d = resp.json()
    assert "impressions" in d
    assert "clicks" in d
    assert "ctr" in d
    assert "today_cpm_revenue_jpy" in d
    assert "device_count" in d
    assert "month_revenue_jpy" in d


@pytest.mark.asyncio
async def test_dealer_stats_today_unauthorized(client):
    resp = await client.get("/mdm/dealer/stats/today?api_key=invalid-key-xyz")
    assert resp.status_code == 401


# ── dealer/push ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dealer_push_no_devices(client, dealer):
    """デバイスなし → sent=0, ok=true"""
    api_key = dealer["api_key"]
    with patch("mdm.router.send_notification", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        resp = await client.post(
            f"/mdm/dealer/push?api_key={api_key}",
            json={"title": "テスト通知", "body": "こんにちは", "url": "https://example.com"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["sent"] == 0
    assert d["total_targeted"] == 0


@pytest.mark.asyncio
async def test_dealer_push_ok(client, dealer):
    """Androidデバイスあり → send_notification呼ばれる"""
    api_key = dealer["api_key"]
    dealer_id = dealer["id"]

    # DBセッションを直接操作して DeviceDB + AndroidDeviceDB をインサート
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        token = "test-token-push-001"
        dev = DeviceDB(
            enrollment_token=token,
            dealer_id=dealer_id,
            platform="android",
            status="active",
        )
        db.add(dev)
        android_dev = AndroidDeviceDB(
            device_id="android-test-001",
            enrollment_token=token,
            fcm_token="fcm-token-test-001",
            status="active",
        )
        db.add(android_dev)
        await db.commit()
        break

    with patch("mdm.router.send_notification", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        resp = await client.post(
            f"/mdm/dealer/push?api_key={api_key}",
            json={"title": "テスト通知", "body": "本文テスト"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["sent"] >= 1
    mock_send.assert_called()


@pytest.mark.asyncio
async def test_dealer_push_rate_limit(client, dealer):
    """月3回超 → 429"""
    api_key = dealer["api_key"]
    dealer_id = dealer["id"]

    # DealerPushLogを3件インサートしてレート制限状態を作る
    from datetime import datetime, timezone
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        for i in range(3):
            log = DealerPushLogDB(
                dealer_id=dealer_id,
                title=f"過去通知{i}",
                body="テスト",
                android_sent=0,
                ios_sent=0,
                total_devices=0,
            )
            db.add(log)
        await db.commit()
        break

    resp = await client.post(
        f"/mdm/dealer/push?api_key={api_key}",
        json={"title": "超過通知", "body": "NG"},
    )
    assert resp.status_code == 429


# ── dealer/webclips ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dealer_webclips_get(client, dealer):
    """GET → キャンペーン自動作成、webclipsフィールドあり"""
    api_key = dealer["api_key"]
    resp = await client.get(f"/mdm/dealer/webclips?api_key={api_key}")
    assert resp.status_code == 200
    d = resp.json()
    assert "campaign_id" in d
    assert "webclips" in d
    assert isinstance(d["webclips"], list)


@pytest.mark.asyncio
async def test_dealer_webclips_put(client, dealer):
    """PUT → webclips更新、_redeploy_campaign呼ばれる"""
    api_key = dealer["api_key"]
    webclips = [
        {"label": "公式サイト", "url": "https://example.com", "icon_url": None},
        {"label": "キャンペーン", "url": "https://example.com/campaign", "icon_url": None},
    ]
    with patch("mdm.router._redeploy_campaign", new_callable=AsyncMock):
        resp = await client.put(
            f"/mdm/dealer/webclips?api_key={api_key}",
            json={"webclips": webclips},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["webclip_count"] == 2

    # GET で確認
    resp2 = await client.get(f"/mdm/dealer/webclips?api_key={api_key}")
    assert resp2.status_code == 200
    assert len(resp2.json()["webclips"]) == 2


@pytest.mark.asyncio
async def test_dealer_webclips_put_too_many(client, dealer):
    """11件 → 422"""
    api_key = dealer["api_key"]
    webclips = [{"label": f"clip{i}", "url": f"https://example.com/{i}"} for i in range(11)]
    resp = await client.put(
        f"/mdm/dealer/webclips?api_key={api_key}",
        json={"webclips": webclips},
    )
    assert resp.status_code == 422
