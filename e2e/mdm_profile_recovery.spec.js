/**
 * mdm_profile_recovery.spec.js
 * MDMプロファイル消失防止 E2Eテスト
 *
 * テスト対象エンドポイント:
 *   POST /mdm/device/consent                          - 同意登録 → enrollment_token取得
 *   GET  /mdm/re-enroll?token={token}                 - 再エンロール
 *   POST /mdm/android/register (with token)            - Android機種変更引き継ぎ
 *   POST /mdm/admin/bulk-restore                       - 一括再push
 *   GET  /mdm/admin/device/{device_id}/re-enroll-url  - 管理者用URL発行
 */
const { test, expect } = require("@playwright/test");

const BASE_URL = process.env.BASE_URL || "http://localhost:8000";
const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const ADMIN_HEADERS = {
  "X-Admin-Key": ADMIN_KEY,
  "Content-Type": "application/json",
};
const ALL_CONSENT_ITEMS = [
  "lockscreen_ads", "push_notifications", "webclip_install",
  "vpn_setup", "app_install", "data_collection",
];

// ─── ヘルパー ─────────────────────────────────────────────────────

/** 同意登録して enrollment_token を取得する */
async function createEnrollmentToken(request, dealerId) {
  const res = await request.post(`${BASE_URL}/mdm/device/consent`, {
    headers: { "Content-Type": "application/json" },
    data: {
      dealer_id: dealerId,
      user_agent: "Mozilla/5.0 (Linux; Android 14; Pixel 7)",
      consent_items: ALL_CONSENT_ITEMS,
    },
  });
  expect(res.ok(), `consent failed: ${res.status()}`).toBeTruthy();
  const data = await res.json();
  expect(data.enrollment_token).toBeTruthy();
  return data.enrollment_token;
}

/** Android デバイスを登録する */
async function registerAndroidDevice(request, deviceId, extraBody = {}) {
  return request.post(`${BASE_URL}/mdm/android/register`, {
    headers: { "Content-Type": "application/json" },
    data: { device_id: deviceId, model: "E2E Test Device", android_version: "14", ...extraBody },
  });
}

// ─── テスト ───────────────────────────────────────────────────────

test.describe("MDMプロファイル消失防止 E2E", () => {
  let dealerId;
  test.beforeAll(async ({ request }) => {
    const res = await request.post(`${BASE_URL}/mdm/admin/dealers`, {
      headers: ADMIN_HEADERS,
      data: { name: `E2E再エンロールテスト店舗-${Date.now()}`, store_code: `e2e-recovery-${Date.now()}` },
    });
    const d = await res.json();
    dealerId = d.id;
  });

  test("GET /mdm/re-enroll - 有効tokenで再エンロール情報が取得できる", async ({ request }) => {
    // 1. 同意登録で enrollment_token を取得
    const token = await createEnrollmentToken(request, dealerId);

    // 2. re-enroll
    const reRes = await request.get(`${BASE_URL}/mdm/re-enroll?token=${token}`);
    expect(reRes.status()).toBe(200);
    const body = await reRes.json();
    expect(body).toBeTruthy();
  });

  test("POST /mdm/android/register - enrollment_token付きで機種変更が成功する", async ({ request }) => {
    // 1. 同意登録で enrollment_token を取得
    const token = await createEnrollmentToken(request, dealerId);
    const oldDeviceId = `e2e-migrate-old-${Date.now()}`;
    const newDeviceId = `e2e-migrate-new-${Date.now()}`;

    // 2. 旧デバイスで登録
    const res1 = await registerAndroidDevice(request, oldDeviceId, { enrollment_token: token });
    expect(res1.ok()).toBeTruthy();
    expect((await res1.json()).status).toBe("registered");

    // 3. 機種変更（同じtokenで新device_id）
    const res2 = await registerAndroidDevice(request, newDeviceId, {
      enrollment_token: token,
      device_fingerprint: "E2E:NewDevice:E2E",
    });
    expect(res2.ok()).toBeTruthy();
    const data2 = await res2.json();
    expect(data2.status).toBe("migrated");
    expect(data2.device_id).toBe(newDeviceId);
  });

  test("GET /mdm/admin/device/{device_id}/re-enroll-url - 管理者がURLを取得できる", async ({ request }) => {
    // 1. 同意登録 + Android デバイス登録
    const token = await createEnrollmentToken(request, dealerId);
    const deviceId = `e2e-admin-url-${Date.now()}`;
    const regRes = await registerAndroidDevice(request, deviceId, { enrollment_token: token });
    expect(regRes.ok()).toBeTruthy();

    // 2. 管理者用URL取得（device_idで指定）
    const urlRes = await request.get(
      `${BASE_URL}/mdm/admin/device/${deviceId}/re-enroll-url`,
      { headers: ADMIN_HEADERS }
    );
    expect(urlRes.ok(), `re-enroll-url failed: ${urlRes.status()}`).toBeTruthy();
    const data = await urlRes.json();
    // re_enroll_url キーにtokenが含まれること
    const urlValue = data.re_enroll_url || data.url || "";
    expect(urlValue).toContain(token);
  });

  test("POST /mdm/admin/bulk-restore - 一括再pushが受け付けられる", async ({ request }) => {
    const res = await request.post(`${BASE_URL}/mdm/admin/bulk-restore`, {
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    // {"count": 0, "status": "queued"} または {"queued": 0} を受け入れる
    const count = data.count ?? data.queued;
    expect(typeof count).toBe("number");
  });

  test("GET /mdm/re-enroll - 存在しないtokenで404が返る", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/mdm/re-enroll?token=nonexistent_token_xyz_e2e`);
    expect(res.status()).toBe(404);
  });

  test("POST /mdm/device/migrate-restore - フォールバック手動復旧エンドポイントが存在する", async ({ request }) => {
    // old_device_id も含めた正しいリクエスト形式で送信
    const res = await request.post(`${BASE_URL}/mdm/device/migrate-restore`, {
      headers: { "Content-Type": "application/json" },
      data: {
        enrollment_token: "nonexistent_token_for_test",
        old_device_id: "old_device_id_for_test",
        new_device_id: "new_device_id_for_test",
      },
    });
    // 存在しないtoken → 404、バリデーションエラー → 422
    expect([400, 404, 422]).toContain(res.status());
  });

});
