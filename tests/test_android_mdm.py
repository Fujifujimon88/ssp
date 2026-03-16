"""Android MDM バックエンド API テスト

フロー:
  1. デバイス登録 (POST /mdm/android/register)
  2. コマンドポーリング・空確認 (GET /mdm/android/commands/{device_id})
  3. 管理者がコマンドをキューイング (POST /mdm/admin/android/push)
  4. DPCがコマンドを取得・実行 (GET /mdm/android/commands/{device_id})
  5. DPCがACK送信 (POST /mdm/android/commands/{command_id}/ack)
  6. ロック画面・ウィジェットコンテンツ取得
  7. デバイス一覧確認 (GET /mdm/admin/android/devices)

実行: cd ssp_platform && pytest tests/test_android_mdm.py -v
"""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

DEVICE_ID = "TEST_ANDROID_PIXEL7_001"
FCM_TOKEN = "fcm-test-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_queued_command_id: str = ""


# ── 1. デバイス登録 ────────────────────────────────────────────

async def test_register_new_device(client: AsyncClient):
    resp = await client.post("/mdm/android/register", json={
        "device_id": DEVICE_ID,
        "fcm_token": FCM_TOKEN,
        "manufacturer": "Google",
        "model": "Pixel 7",
        "android_version": "14",
        "sdk_int": 34,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["device_id"] == DEVICE_ID


async def test_register_same_device_updates(client: AsyncClient):
    """同じデバイスIDの場合はFCMトークンが更新される"""
    new_token = "fcm-test-token-new-yyyyyyyyyyyyyy"
    resp = await client.post("/mdm/android/register", json={
        "device_id": DEVICE_ID,
        "fcm_token": new_token,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"


# ── 2. コマンドポーリング（空） ────────────────────────────────

async def test_poll_commands_empty(client: AsyncClient):
    """登録直後はコマンドキューが空"""
    resp = await client.get(f"/mdm/android/commands/{DEVICE_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert "commands" in data
    assert isinstance(data["commands"], list)
    assert len(data["commands"]) == 0


# ── 3. 管理者コマンドキューイング ────────────────────────────────

async def test_admin_enqueue_webclip_command(client: AsyncClient, admin_key: str):
    """管理者がWebクリップ追加コマンドをキューイングできる"""
    global _queued_command_id
    resp = await client.post(
        "/mdm/admin/android/push",
        json={
            "device_id": DEVICE_ID,
            "command_type": "add_webclip",
            "payload": {"url": "https://example.com", "label": "テストApp"},
            "send_fcm": False,  # テスト中はFCM送信しない
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert "command_id" in data
    _queued_command_id = data["command_id"]


async def test_admin_enqueue_notification_command(client: AsyncClient, admin_key: str):
    """通知コマンドもキューイングできる"""
    resp = await client.post(
        "/mdm/admin/android/push",
        json={
            "device_id": DEVICE_ID,
            "command_type": "show_notification",
            "payload": {"title": "お得情報", "body": "NordVPN 30%オフ！"},
            "send_fcm": False,
        },
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


async def test_admin_push_unknown_device_returns_404(client: AsyncClient, admin_key: str):
    resp = await client.post(
        "/mdm/admin/android/push",
        json={"device_id": "NONEXISTENT", "command_type": "add_webclip", "payload": {}},
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 404


async def test_admin_push_without_key_returns_403(client: AsyncClient):
    resp = await client.post(
        "/mdm/admin/android/push",
        json={"device_id": DEVICE_ID, "command_type": "add_webclip", "payload": {}},
    )
    assert resp.status_code in (401, 403)


# ── 4. DPCがコマンド取得（pending→sent） ─────────────────────

async def test_poll_commands_returns_queued_commands(client: AsyncClient):
    """キューイング後のポーリングでコマンドが返る"""
    resp = await client.get(f"/mdm/android/commands/{DEVICE_ID}")
    assert resp.status_code == 200
    data = resp.json()
    commands = data["commands"]
    assert len(commands) >= 1

    # コマンド構造の確認
    cmd = commands[0]
    assert "id" in cmd
    assert "type" in cmd
    assert "payload" in cmd
    assert cmd["type"] in ("add_webclip", "show_notification", "install_apk", "update_lockscreen")


async def test_poll_second_time_returns_empty(client: AsyncClient):
    """2回目のポーリングでは既にsentになっているためpendingが0件"""
    resp = await client.get(f"/mdm/android/commands/{DEVICE_ID}")
    assert resp.status_code == 200
    # 一度取得済みはsent扱いで再取得されない
    assert len(resp.json()["commands"]) == 0


# ── 5. ACK送信 ─────────────────────────────────────────────────

async def test_ack_command_success(client: AsyncClient):
    """DPCがコマンド実行成功をACK送信できる"""
    assert _queued_command_id, "command_id が取得されていない"
    resp = await client.post(
        f"/mdm/android/commands/{_queued_command_id}/ack",
        json={"success": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_ack_nonexistent_command_returns_404(client: AsyncClient):
    resp = await client.post(
        "/mdm/android/commands/nonexistent-uuid/ack",
        json={"success": True},
    )
    assert resp.status_code == 404


# ── 6. ロック画面・ウィジェットコンテンツ ─────────────────────

async def test_lockscreen_content_returns_valid_response(client: AsyncClient):
    """ロック画面アプリのコンテンツ取得（案件0件でもエラーにならない）"""
    resp = await client.get(
        "/mdm/android/lockscreen/content",
        params={"device_id": DEVICE_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data  # None か dict


async def test_widget_content_returns_valid_response(client: AsyncClient):
    """ウィジェットコンテンツ取得"""
    resp = await client.get(
        "/mdm/android/widget/content",
        params={"device_id": DEVICE_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


# ── 7. 管理者デバイス一覧 ─────────────────────────────────────

async def test_admin_list_devices(client: AsyncClient, admin_key: str):
    resp = await client.get(
        "/mdm/admin/android/devices",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp.status_code == 200
    devices = resp.json()
    assert isinstance(devices, list)
    assert len(devices) >= 1

    our_device = next((d for d in devices if d["device_id"] == DEVICE_ID), None)
    assert our_device is not None
    assert our_device["model"] == "Pixel 7"
    assert our_device["android_version"] == "14"


async def test_admin_list_devices_without_key_returns_403(client: AsyncClient):
    resp = await client.get("/mdm/admin/android/devices")
    assert resp.status_code in (401, 403)


# ── 8. エンロールポータルAndroidフロー ───────────────────────

async def test_portal_returns_html(client: AsyncClient):
    """エンロールポータルがHTMLを返す"""
    resp = await client.get("/mdm/portal")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "サービス設定" in resp.text


async def test_device_consent_returns_android_apk_url(client: AsyncClient):
    """Android UAで同意するとandroid_apk_urlが返る"""
    resp = await client.post("/mdm/device/consent", json={
        "age_group": "20s",
        "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36",
        "consent_items": [
            "lockscreen_ads", "push_notifications", "webclip_install",
            "vpn_setup", "app_install", "data_collection",
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "enrollment_token" in data
    assert "android_apk_url" in data
    assert "line_add_friend_url" in data
    assert "/mdm/android/dpc.apk" in data["android_apk_url"]
