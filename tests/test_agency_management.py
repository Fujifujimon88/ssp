"""代理店管理 API テスト（8件）

テスト対象:
  POST /mdm/admin/agencies          — 代理店登録
  GET  /mdm/admin/agencies          — 代理店一覧
  GET  /mdm/admin/agencies-with-stores — 店舗付き代理店一覧
  POST /mdm/admin/agencies/{id}/stores — 店舗追加

実行: cd ssp_platform && pytest tests/test_agency_management.py -v
"""
import pytest
import pytest_asyncio

from config import settings


# ── フィクスチャ ─────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def agency(client):
    """テスト用代理店を作成して返す"""
    headers = {"X-Admin-Key": settings.admin_api_key}
    resp = await client.post(
        "/mdm/admin/agencies",
        json={"name": "テスト代理店", "contact_email": "agency@example.com"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── POST /mdm/admin/agencies ────────────────────────────────────

@pytest.mark.asyncio
async def test_create_agency_ok(client, admin_key):
    """正常系: name + email → 200, id/name/api_key を返す"""
    resp = await client.post(
        "/mdm/admin/agencies",
        json={"name": "別テスト代理店", "contact_email": "other@example.com"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data
    assert "name" in data
    assert "api_key" in data
    assert data["name"] == "別テスト代理店"


@pytest.mark.asyncio
async def test_create_agency_no_name(client, admin_key):
    """name が空の場合 → 400"""
    resp = await client.post(
        "/mdm/admin/agencies",
        json={"name": "", "contact_email": "noname@example.com"},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_create_agency_unauthorized(client):
    """Adminキーなし → 401 または 403"""
    resp = await client.post(
        "/mdm/admin/agencies",
        json={"name": "不正アクセス代理店", "contact_email": "unauth@example.com"},
    )
    assert resp.status_code in (401, 403), resp.text


# ── GET /mdm/admin/agencies ─────────────────────────────────────

@pytest.mark.asyncio
async def test_list_agencies_ok(client, admin_key, agency):
    """正常系: GET → 200, agencies はリスト, 作成済み代理店を含む"""
    resp = await client.get(
        "/mdm/admin/agencies",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "agencies" in data
    assert isinstance(data["agencies"], list)
    ids = [a["id"] for a in data["agencies"]]
    assert agency["id"] in ids


@pytest.mark.asyncio
async def test_list_agencies_unauthorized(client):
    """Adminキーなし → 401 または 403"""
    resp = await client.get("/mdm/admin/agencies")
    assert resp.status_code in (401, 403), resp.text


# ── GET /mdm/admin/agencies-with-stores ─────────────────────────

@pytest.mark.asyncio
async def test_agencies_with_stores_ok(client, admin_key, agency):
    """正常系: GET → 200, agencies リストの各要素に stores フィールドがある"""
    resp = await client.get(
        "/mdm/admin/agencies-with-stores",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "agencies" in data
    assert isinstance(data["agencies"], list)
    # 作成済み代理店が含まれ、stores フィールドを持つこと
    matched = [a for a in data["agencies"] if a["id"] == agency["id"]]
    assert len(matched) == 1
    assert "stores" in matched[0]


# ── POST /mdm/admin/agencies/{agency_id}/stores ─────────────────

@pytest.mark.asyncio
async def test_add_store_to_agency_ok(client, admin_key, agency):
    """正常系: 1件目の店舗追加 → 200, store_number=1"""
    agency_id = agency["id"]
    resp = await client.post(
        f"/mdm/admin/agencies/{agency_id}/stores",
        json={
            "name": "テスト店舗1号店",
            "store_code": "AGENCY-STORE-001",
            "address": "東京都千代田区1-1-1",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data
    assert "name" in data
    assert "store_code" in data
    assert "store_number" in data
    assert "agency_id" in data
    assert "api_key" in data
    assert data["store_number"] == 1
    assert data["agency_id"] == agency_id


@pytest.mark.asyncio
async def test_add_second_store_increments_number(client, admin_key, agency):
    """2件目の店舗追加 → store_number=2"""
    agency_id = agency["id"]
    resp = await client.post(
        f"/mdm/admin/agencies/{agency_id}/stores",
        json={
            "name": "テスト店舗2号店",
            "store_code": "AGENCY-STORE-002",
            "address": "東京都千代田区2-2-2",
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["store_number"] == 2
