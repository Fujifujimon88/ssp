"""Feature 4: 店舗ロック画面専用枠 テスト（15件）

テスト対象:
  POST /mdm/admin/stores/{dealer_id}/lockscreen-creative  — 店舗クリエイティブ登録
  GET  /mdm/admin/stores/{dealer_id}/lockscreen-creatives — 一覧取得
  PATCH /mdm/admin/stores/{dealer_id}/lockscreen-creatives/{id}/status — ステータス変更
  DELETE /mdm/admin/stores/{dealer_id}/ad-assignments/{id} — 削除（既存エンドポイント）
  selector.select_creative() 優先配信ロジック
"""
import pytest
import pytest_asyncio

from db_models import (
    AffiliateCampaignDB,
    AndroidDeviceDB,
    CampaignDB,
    CreativeDB,
    DealerDB,
    DeviceDB,
    MdmAdSlotDB,
    StoreAdAssignmentDB,
)


# ── フィクスチャ ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def dealer(client, admin_key):
    resp = await client.post(
        "/mdm/admin/dealers",
        json={"name": "テスト店舗LS", "store_code": "LS-001", "address": "東京都"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest_asyncio.fixture(scope="module")
async def other_dealer(client, admin_key):
    resp = await client.post(
        "/mdm/admin/dealers",
        json={"name": "別店舗", "store_code": "LS-002", "address": "大阪府"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest_asyncio.fixture(scope="module")
async def lockscreen_slot(client):
    """ロック画面スロット定義を DB に直接作成"""
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        slot = MdmAdSlotDB(
            name="Androidロック画面",
            slot_type="lockscreen",
            floor_price_cpm=500.0,
            status="active",
        )
        db.add(slot)
        await db.commit()
        await db.refresh(slot)
        return {"id": slot.id}


@pytest_asyncio.fixture(scope="module")
async def enrolled_device(client, dealer):
    """dealer に紐付いた Android デバイスを DB に直接作成"""
    from main import app
    from database import get_db

    async for db in app.dependency_overrides[get_db]():
        token = "store-ls-test-token-001"
        dev = DeviceDB(
            enrollment_token=token,
            dealer_id=dealer["id"],
            platform="android",
            status="active",
        )
        db.add(dev)
        android_dev = AndroidDeviceDB(
            device_id="store-ls-android-001",
            enrollment_token=token,
            fcm_token="fcm-store-ls-001",
            status="active",
        )
        db.add(android_dev)
        await db.commit()
        return {"device_id": "store-ls-android-001", "token": token}


# ── POST /admin/stores/{dealer_id}/lockscreen-creative ───────────────────────


@pytest.mark.asyncio
async def test_create_store_creative_ok(client, admin_key, dealer):
    """正常系: 店舗クリエイティブを登録できる"""
    resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "本日限定セール！",
            "image_url": "https://example.com/sale.jpg",
            "click_url": "https://example.com/sale",
            "slot_type": "lockscreen",
            "priority": 1,
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert "assignment_id" in d
    assert "campaign_id" in d
    assert "creative_id" in d
    assert d["dealer_id"] == dealer["id"]
    assert d["priority"] == 1
    assert d["slot_type"] == "lockscreen"


@pytest.mark.asyncio
async def test_create_store_creative_invalid_slot(client, admin_key, dealer):
    """異常系: slot_type が不正 → 422"""
    resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "テスト",
            "image_url": "https://example.com/img.jpg",
            "click_url": "https://example.com",
            "slot_type": "notification",  # 不正
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_store_creative_dealer_not_found(client, admin_key):
    """異常系: dealer_id が存在しない → 404"""
    resp = await client.post(
        "/mdm/admin/stores/nonexistent-dealer-id/lockscreen-creative",
        json={
            "title": "テスト",
            "image_url": "https://example.com/img.jpg",
            "click_url": "https://example.com",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_store_creative_no_admin_key(client, dealer):
    """認証なし → 401"""
    resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={"title": "T", "image_url": "https://x.com/x.jpg", "click_url": "https://x.com"},
    )
    assert resp.status_code == 401


# ── GET /admin/stores/{dealer_id}/lockscreen-creatives ───────────────────────


@pytest.mark.asyncio
async def test_list_store_creatives_ok(client, admin_key, dealer):
    """登録済みクリエイティブが返る"""
    # まず1件作成
    await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "夕方セール",
            "image_url": "https://example.com/eve.jpg",
            "click_url": "https://example.com/eve",
            "priority": 2,
        },
        headers={"X-Admin-Key": admin_key},
    )

    resp = await client.get(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert "creatives" in d
    assert len(d["creatives"]) >= 1
    # 各クリエイティブに必要フィールドがある
    for c in d["creatives"]:
        assert "assignment_id" in c
        assert "image_url" in c
        assert "status" in c


@pytest.mark.asyncio
async def test_list_store_creatives_empty_for_other_dealer(client, admin_key, other_dealer):
    """別店舗のクリエイティブは返らない"""
    resp = await client.get(
        f"/mdm/admin/stores/{other_dealer['id']}/lockscreen-creatives",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["creatives"] == []


# ── PATCH .../status ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_store_creative(client, admin_key, dealer):
    """クリエイティブを一時停止できる"""
    # 作成
    create_resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "一時停止テスト",
            "image_url": "https://example.com/pause.jpg",
            "click_url": "https://example.com/pause",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assignment_id = create_resp.json()["assignment_id"]

    # 一時停止
    resp = await client.patch(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives/{assignment_id}/status",
        json={"status": "paused"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # 再アクティブ
    resp2 = await client.patch(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives/{assignment_id}/status",
        json={"status": "active"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "active"


@pytest.mark.asyncio
async def test_pause_invalid_status(client, admin_key, dealer):
    """無効な status 値 → 422"""
    create_resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "バリデーションテスト",
            "image_url": "https://example.com/v.jpg",
            "click_url": "https://example.com/v",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assignment_id = create_resp.json()["assignment_id"]

    resp = await client.patch(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives/{assignment_id}/status",
        json={"status": "deleted"},  # 不正
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 422


# ── select_creative() 店舗専用枠優先ロジック ──────────────────────────────────


@pytest.mark.asyncio
async def test_store_creative_takes_priority(client, admin_key, dealer, enrolled_device, lockscreen_slot):
    """
    店舗専用クリエイティブが登録されている場合、通常のアフィリエイト広告より優先される。
    GET /mdm/android/lockscreen/content に enrollment_token を渡すと
    店舗専用クリエイティブが返り、is_store_creative=True になる。
    """
    from main import app
    from database import get_db
    from sqlalchemy import select as sa_select

    # 通常のアフィリエイトキャンペーン + クリエイティブを作成
    async for db in app.dependency_overrides[get_db]():
        normal_campaign = AffiliateCampaignDB(
            name="通常アフィリエイト広告",
            category="app",
            destination_url="https://affiliate.example.com",
            reward_type="cpi",
            reward_amount=500.0,
            status="active",
        )
        db.add(normal_campaign)
        await db.flush()
        normal_creative = CreativeDB(
            campaign_id=normal_campaign.id,
            name="通常クリエイティブ",
            type="image",
            title="アプリをダウンロード",
            image_url="https://example.com/app.jpg",
            click_url="https://affiliate.example.com/dl",
            status="active",
        )
        db.add(normal_creative)
        await db.commit()
        break

    # 店舗専用クリエイティブを登録
    create_resp = await client.post(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creative",
        json={
            "title": "本日タイムセール！",
            "image_url": "https://example.com/timesale.jpg",
            "click_url": "https://example.com/timesale",
            "priority": 1,
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert create_resp.status_code == 200
    store_creative_id = create_resp.json()["creative_id"]

    # ロック画面コンテンツを取得（enrollment_token でデバイスを特定）
    token = enrolled_device["token"]
    resp = await client.get(
        f"/mdm/android/lockscreen/content?token={token}",
    )
    assert resp.status_code == 200
    content = resp.json().get("content", {})

    # 店舗専用クリエイティブが返ること
    assert content.get("is_store_creative") is True, (
        f"Expected store creative, got: {content}"
    )


@pytest.mark.asyncio
async def test_store_creative_paused_falls_through(client, admin_key, dealer, enrolled_device):
    """
    店舗専用クリエイティブが paused の場合、通常の広告にフォールバックする。
    """
    # 全店舗クリエイティブを paused にする
    list_resp = await client.get(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives",
        headers={"X-Admin-Key": admin_key},
    )
    for c in list_resp.json()["creatives"]:
        await client.patch(
            f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives/{c['assignment_id']}/status",
            json={"status": "paused"},
            headers={"X-Admin-Key": admin_key},
        )

    token = enrolled_device["token"]
    resp = await client.get(
        f"/mdm/android/lockscreen/content?token={token}",
    )
    assert resp.status_code == 200
    # is_store_creative が False または存在しない（通常広告）
    content = resp.json().get("content", {})
    assert content.get("is_store_creative") is not True

    # テスト後: 元に戻す
    list_resp2 = await client.get(
        f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives",
        headers={"X-Admin-Key": admin_key},
    )
    for c in list_resp2.json()["creatives"]:
        await client.patch(
            f"/mdm/admin/stores/{dealer['id']}/lockscreen-creatives/{c['assignment_id']}/status",
            json={"status": "active"},
            headers={"X-Admin-Key": admin_key},
        )


@pytest.mark.asyncio
async def test_store_creative_not_shown_to_other_dealer(client, admin_key, other_dealer):
    """
    別店舗のデバイスには当該店舗の専用クリエイティブが表示されない。
    """
    from main import app
    from database import get_db

    # 別店舗デバイスを作成
    async for db in app.dependency_overrides[get_db]():
        token = "other-dealer-token-999"
        dev = DeviceDB(
            enrollment_token=token,
            dealer_id=other_dealer["id"],
            platform="android",
            status="active",
        )
        db.add(dev)
        await db.commit()
        break

    resp = await client.get(
        "/mdm/android/lockscreen/content?token=other-dealer-token-999",
    )
    assert resp.status_code == 200
    content = resp.json().get("content", {})
    # dealer["id"] の店舗専用クリエイティブは表示されない
    assert content.get("is_store_creative") is not True
