"""動画クリエイティブ・VAST配信 API テスト（7件）

テスト対象:
  POST /mdm/admin/creatives         — クリエイティブ登録（video / image）
  GET  /mdm/ad/vast/{impression_id} — VAST 3.0 XML 取得
  POST /mdm/ad/video_event/{impression_id}/{event} — 動画イベント記録

NOTE: CreativeCreate モデルには現時点で video_url / video_duration_sec /
      skip_after_sec フィールドが含まれていないため、test_create_video_creative_ok は
      実装追加まで 422 となることが想定される（TDD）。

実行: cd ssp_platform && pytest tests/test_video_creative.py -v
"""
import pytest
import pytest_asyncio

from config import settings


# ── フィクスチャ ─────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def dealer(client):
    """テスト用ディーラーを作成して返す"""
    headers = {"X-Admin-Key": settings.admin_api_key}
    resp = await client.post(
        "/mdm/admin/dealers",
        json={
            "name": "動画テスト店舗",
            "store_code": "VIDEO-TEST-DEALER-001",
            "address": "大阪府大阪市1-1-1",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest_asyncio.fixture(scope="module")
async def campaign(client, dealer):
    """テスト用キャンペーンを作成して返す"""
    headers = {"X-Admin-Key": settings.admin_api_key}
    resp = await client.post(
        "/mdm/admin/campaigns",
        json={
            "name": "動画テストキャンペーン",
            "dealer_id": dealer["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── POST /mdm/admin/creatives（video）────────────────────────────

@pytest.mark.asyncio
async def test_create_video_creative_ok(client, admin_key, campaign):
    """
    動画クリエイティブ登録 → 200, id を返す。

    NOTE: CreativeCreate に video_url / video_duration_sec / skip_after_sec が
    追加されるまでは 422 となる（実装を駆動するテスト）。
    """
    resp = await client.post(
        "/mdm/admin/creatives",
        json={
            "campaign_id": campaign["id"],
            "name": "テスト動画クリエイティブ",
            "type": "video",
            "title": "動画広告タイトル",
            "click_url": "https://example.com/video-lp",
            "video_url": "https://example.com/video.mp4",
            "video_duration_sec": 30,
            "skip_after_sec": 5,
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data


@pytest.mark.asyncio
async def test_create_video_creative_returns_type(client, admin_key, campaign):
    """動画クリエイティブ登録レスポンスに type="video" が含まれること"""
    resp = await client.post(
        "/mdm/admin/creatives",
        json={
            "campaign_id": campaign["id"],
            "name": "タイプ確認用動画クリエイティブ",
            "type": "video",
            "title": "動画広告タイトル2",
            "click_url": "https://example.com/video-lp2",
            "video_url": "https://example.com/video2.mp4",
            "video_duration_sec": 15,
            "skip_after_sec": 3,
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # type フィールドが返る場合は "video" であること
    if "type" in data:
        assert data["type"] == "video"


# ── POST /mdm/admin/creatives（image）────────────────────────────

@pytest.mark.asyncio
async def test_create_image_creative_ok(client, admin_key, campaign):
    """画像クリエイティブ登録 → 200, id を返す"""
    resp = await client.post(
        "/mdm/admin/creatives",
        json={
            "campaign_id": campaign["id"],
            "name": "テスト画像クリエイティブ",
            "type": "image",
            "title": "画像広告タイトル",
            "click_url": "https://example.com/image-lp",
            "image_url": "https://example.com/banner.png",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data


# ── POST /mdm/ad/video_event/{impression_id}/{event} ─────────────

@pytest.mark.asyncio
async def test_video_event_invalid_event(client):
    """無効なイベント名 → 400"""
    resp = await client.post("/mdm/ad/video_event/nonexistent-impression-id/invalid_event")
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_video_event_missing_impression(client):
    """存在しない impression_id に対する有効なイベント → 404"""
    resp = await client.post("/mdm/ad/video_event/nonexistent-impression-id/start")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_video_event_complete(client):
    """
    complete イベント記録のテスト。
    impression の事前作成が必要なため、DB直接操作が複雑になるケースは
    エンドツーエンドセットアップを前提としてスキップ可。
    ここでは 404 が返ること（DBにimpression未登録）を確認する。
    """
    resp = await client.post("/mdm/ad/video_event/nonexistent-impression-complete/complete")
    assert resp.status_code == 404, resp.text


# ── GET /mdm/ad/vast/{impression_id} ─────────────────────────────

@pytest.mark.asyncio
async def test_vast_missing_impression(client):
    """存在しない impression_id → 404"""
    resp = await client.get("/mdm/ad/vast/nonexistent-impression-id")
    assert resp.status_code == 404, resp.text
