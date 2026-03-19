"""Feature 6: iOS ウィジェット広告 管理API テスト（10件）

テスト対象:
  POST /mdm/admin/ios/widget/creative  — クリエイティブ登録
  GET  /mdm/admin/ios/widget/stats     — インプレッション統計
  GET  /mdm/admin/ios/widget/preview   — 配信プレビュー（ドライラン）
  GET  /mdm/ios/widget_content/{device_id}  — WidgetKit エンドポイント（既存 + 検証）
"""
import pytest
import pytest_asyncio

from db_models import AffiliateCampaignDB, CreativeDB, iOSDeviceDB, MdmAdSlotDB, MdmImpressionDB


# ── フィクスチャ ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def ios_device(client):
    """テスト用 iOS デバイスを DB に直接作成"""
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        device = iOSDeviceDB(
            udid="ios-widget-test-udid-001",
            enrollment_token="ios-widget-token-001",
            enrolled=True,
            status="active",
        )
        db.add(device)
        await db.commit()
        return {"udid": "ios-widget-test-udid-001", "token": "ios-widget-token-001"}


@pytest_asyncio.fixture(scope="module")
async def widget_slot(client):
    """webclip_ios スロット定義を作成"""
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        slot = MdmAdSlotDB(
            name="iOS ウィジェット",
            slot_type="webclip_ios",
            floor_price_cpm=800.0,
            status="active",
        )
        db.add(slot)
        await db.commit()
        await db.refresh(slot)
        return {"id": slot.id}


# ── POST /admin/ios/widget/creative ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_ios_widget_creative_ok(client, admin_key):
    """正常系: iOS ウィジェットクリエイティブを登録できる"""
    resp = await client.post(
        "/mdm/admin/ios/widget/creative",
        json={
            "title": "iOSウィジェット広告テスト",
            "image_url": "https://example.com/widget.jpg",
            "click_url": "https://example.com/widget-dest",
            "body": "タップしてチェック",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert "campaign_id" in d
    assert "creative_id" in d
    assert d["title"] == "iOSウィジェット広告テスト"
    assert d["image_url"] == "https://example.com/widget.jpg"


@pytest.mark.asyncio
async def test_create_ios_widget_creative_no_auth(client):
    """認証なし → 401"""
    resp = await client.post(
        "/mdm/admin/ios/widget/creative",
        json={
            "title": "テスト",
            "image_url": "https://example.com/img.jpg",
            "click_url": "https://example.com",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_ios_widget_creative_missing_field(client, admin_key):
    """必須フィールド欠損 → 422"""
    resp = await client.post(
        "/mdm/admin/ios/widget/creative",
        json={
            "title": "タイトルのみ",
            # image_url, click_url が欠損
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_ios_widget_creative_stored_correctly(client, admin_key):
    """
    登録後、DB に category=ios_widget のキャンペーンと image タイプのクリエイティブが作成される。
    """
    from main import app
    from database import get_db
    from sqlalchemy import select as sa_select

    resp = await client.post(
        "/mdm/admin/ios/widget/creative",
        json={
            "title": "DB確認テスト",
            "image_url": "https://example.com/db-check.jpg",
            "click_url": "https://example.com/db-check",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    campaign_id = resp.json()["campaign_id"]
    creative_id = resp.json()["creative_id"]

    async for db in app.dependency_overrides[get_db]():
        campaign = await db.get(AffiliateCampaignDB, campaign_id)
        assert campaign is not None
        assert campaign.category == "ios_widget"

        creative = await db.get(CreativeDB, creative_id)
        assert creative is not None
        assert creative.type == "image"
        assert creative.image_url == "https://example.com/db-check.jpg"
        break


# ── GET /admin/ios/widget/stats ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ios_widget_stats_ok(client, admin_key):
    """正常系: 統計エンドポイントが正しいフィールドを返す"""
    resp = await client.get(
        "/mdm/admin/ios/widget/stats",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert "total_impressions" in d
    assert "total_clicks" in d
    assert "ctr" in d
    assert "top_creatives" in d
    assert isinstance(d["top_creatives"], list)
    assert d["period_days"] == 30  # デフォルト30日


@pytest.mark.asyncio
async def test_ios_widget_stats_with_days_param(client, admin_key):
    """days パラメータが反映される"""
    resp = await client.get(
        "/mdm/admin/ios/widget/stats?days=7",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 7


@pytest.mark.asyncio
async def test_ios_widget_stats_invalid_days(client, admin_key):
    """days=0 → 422"""
    resp = await client.get(
        "/mdm/admin/ios/widget/stats?days=0",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ios_widget_stats_no_auth(client):
    """認証なし → 401"""
    resp = await client.get("/mdm/admin/ios/widget/stats")
    assert resp.status_code == 401


# ── GET /admin/ios/widget/preview ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ios_widget_preview_ok(client, admin_key):
    """ドライランプレビューが返る（impression は記録されない）"""
    from main import app
    from database import get_db
    from sqlalchemy import select as sa_select

    # 事前インプレッション数を記録
    async for db in app.dependency_overrides[get_db]():
        count_before = await db.scalar(
            sa_select(__import__("sqlalchemy", fromlist=["func"]).func.count(MdmImpressionDB.id))
        )
        break

    resp = await client.get(
        "/mdm/admin/ios/widget/preview",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["note"] == "dry_run — impressions are NOT recorded"
    assert "preview_items" in d
    assert isinstance(d["preview_items"], list)

    # インプレッションが増えていないこと（ドライラン確認）
    async for db in app.dependency_overrides[get_db]():
        count_after = await db.scalar(
            sa_select(__import__("sqlalchemy", fromlist=["func"]).func.count(MdmImpressionDB.id))
        )
        break
    assert count_after == count_before


@pytest.mark.asyncio
async def test_ios_widget_preview_with_token(client, admin_key, ios_device):
    """enrollment_token 付きプレビューも正常に返る"""
    resp = await client.get(
        f"/mdm/admin/ios/widget/preview?token={ios_device['token']}",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["enrollment_token"] == ios_device["token"]


# ── GET /mdm/ios/widget_content/{device_id} (既存エンドポイント検証) ──────────


@pytest.mark.asyncio
async def test_ios_widgetkit_content_structure(client, ios_device):
    """
    WidgetKit エンドポイントが正しいレスポンス構造を返す。
    ad フィールドは None または image_url を持つ dict。
    """
    resp = await client.get(
        f"/mdm/ios/widget_content/{ios_device['udid']}",
    )
    assert resp.status_code == 200
    d = resp.json()
    assert "device_id" in d
    assert "points_balance" in d
    assert "coupon_count" in d
    assert "updated_at" in d
    assert "refresh_interval_minutes" in d
    assert d["device_id"] == ios_device["udid"]
    assert isinstance(d["points_balance"], int)
    assert isinstance(d["coupon_count"], int)
    assert d["refresh_interval_minutes"] == 30
    if d["ad"] is not None:
        assert "image_url" in d["ad"]
        assert "title" in d["ad"]
        assert "cta_url" in d["ad"]
