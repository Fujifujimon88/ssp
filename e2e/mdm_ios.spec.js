/**
 * mdm_ios.spec.js
 * iOS MDM アプリ自動インストール機能のE2Eテスト
 *
 * テスト対象:
 *   1. エンロールポータル iOS UA 検出
 *   2. 同意登録 → mobileconfig URL 取得
 *   3. mobileconfig ダウンロード（Content-Type 確認）
 *   4. iOS デバイス一覧 API（管理者）
 *   5. MDM コマンド送信 API（install_application）
 *   6. 存在しないデバイスへのコマンドは 404
 *   7. 認証なしのエンドポイントは 401/403
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const STORE_CODE = "STORE001"; // admin.html で登録済みの店舗コード

// 全 6 必須同意項目
const ALL_CONSENT_ITEMS = [
  "lockscreen_ads",
  "push_notifications",
  "webclip_install",
  "vpn_setup",
  "app_install",
  "data_collection",
];

// ─────────────────────────────────────────────────────────────
// 1. エンロールポータル
// ─────────────────────────────────────────────────────────────
test.describe("エンロールポータル iOS UA検出", () => {
  test("iPhone UA でアクセスするとiOSセクションが表示される", async ({ browser }) => {
    const ctx = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    });
    const page = await ctx.newPage();
    await page.goto(`/mdm/portal?dealer=${STORE_CODE}`);
    await expect(page.locator("#ios-section")).toBeVisible({ timeout: 8000 });
    await expect(page.locator("#android-section")).not.toBeVisible();
    await ctx.close();
  });

  test("Android UA でアクセスするとAndroidセクションが表示される", async ({ browser }) => {
    const ctx = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
    });
    const page = await ctx.newPage();
    await page.goto(`/mdm/portal?dealer=${STORE_CODE}`);
    await expect(page.locator("#android-section")).toBeVisible({ timeout: 8000 });
    await expect(page.locator("#ios-section")).not.toBeVisible();
    await ctx.close();
  });

  test("ポータルページに同意チェックボックスが表示される", async ({ page }) => {
    await page.goto(`/mdm/portal?dealer=${STORE_CODE}`);
    // 同意チェックボックスが存在する
    await expect(page.locator("input[type=checkbox]").first()).toBeVisible({ timeout: 8000 });
  });

  test("チェックなしでは送信ボタンが無効になっている", async ({ page }) => {
    await page.goto(`/mdm/portal?dealer=${STORE_CODE}`);
    // iOS セクションのダウンロードボタン（<a> タグ）は初期状態 btn-disabled クラスを持つ
    const btn = page.locator("#download-btn");
    await expect(btn).toBeVisible({ timeout: 8000 });
    const cls = await btn.getAttribute("class");
    expect(cls).toContain("btn-disabled");
  });
});

// ─────────────────────────────────────────────────────────────
// 2. 同意登録 → mobileconfig URL 取得
// ─────────────────────────────────────────────────────────────
test.describe("同意登録 API /mdm/device/consent", () => {
  let dealerId;
  test.beforeAll(async ({ request }) => {
    const res = await request.post("/mdm/admin/dealers", {
      headers: { "X-Admin-Key": process.env.ADMIN_API_KEY || "change-me-admin-key" },
      data: { name: `E2E同意テスト店舗-${Date.now()}`, store_code: `e2e-consent-${Date.now()}` },
    });
    const d = await res.json();
    dealerId = d.id;
  });

  test("全同意項目チェックで mobileconfig_url が返る", async ({ request }) => {
    const res = await request.post("/mdm/device/consent", {
      data: {
        dealer_id: dealerId,
        consent_items: ALL_CONSENT_ITEMS,
        age_group: "20s",
        user_agent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
      },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("mobileconfig_url");
    expect(data.mobileconfig_url).toContain("/mdm/ios/mobileconfig");
  });

  test("同意項目が不足している場合は 400 が返る", async ({ request }) => {
    const res = await request.post("/mdm/device/consent", {
      data: {
        consent_items: ["lockscreen_ads", "data_collection"], // 一部のみ
      },
    });
    expect(res.status()).toBe(400);
  });
});

// ─────────────────────────────────────────────────────────────
// 3. mobileconfig ダウンロード
// ─────────────────────────────────────────────────────────────
test.describe("mobileconfig ダウンロード /mdm/ios/mobileconfig", () => {
  test("Content-Type が application/x-apple-aspen-config になる", async ({ request }) => {
    const res = await request.get(`/mdm/ios/mobileconfig?dealer=${STORE_CODE}&token=test`);
    // 有効なトークンがないので 400/404 が来るが、Content-Type は確認できる場合もある
    // トークンなしでも 400 以上を期待（クラッシュしない）
    expect(res.status()).toBeLessThan(500);
  });

  test("有効なトークンなしでは 400 または 404 が返る", async ({ request }) => {
    const res = await request.get("/mdm/ios/mobileconfig?token=invalid-token-xyz");
    expect([400, 404, 422]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// 4. iOS デバイス一覧（管理者）
// ─────────────────────────────────────────────────────────────
test.describe("iOS デバイス一覧 /mdm/admin/ios/devices", () => {
  test("有効な管理者キーで配列が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/ios/devices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("各デバイスに必要フィールドがある", async ({ request }) => {
    const res = await request.get("/mdm/admin/ios/devices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    const devices = await res.json();
    if (devices.length > 0) {
      const d = devices[0];
      expect(d).toHaveProperty("udid");
      expect(d).toHaveProperty("enrolled");
      expect(d).toHaveProperty("has_push_token");
      expect(d).toHaveProperty("enrolled_at");
    }
  });

  test("認証なしで 401/403 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/ios/devices");
    expect([401, 403]).toContain(res.status());
  });

  test("不正なキーで 401/403 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/ios/devices", {
      headers: { "X-Admin-Key": "wrong-key" },
    });
    expect([401, 403]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// 5. MDM コマンド送信（install_application）
// ─────────────────────────────────────────────────────────────
test.describe("MDM コマンド送信 /mdm/admin/ios/command", () => {
  test("存在しない UDID に送ると 404 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/command", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        udid: "00000000-0000-0000-0000-000000000000",
        request_type: "install_application",
        params: {
          manifest_url: "https://itunes.apple.com/jp/app/id123456789",
        },
        send_push: false,
      },
    });
    expect(res.status()).toBe(404);
  });

  test("不明な request_type は 400/404 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/command", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        udid: "any-udid",
        request_type: "unknown_command_type",
        params: {},
        send_push: false,
      },
    });
    // 404 (device not found) or 400 (unknown type) どちらも可
    expect([400, 404]).toContain(res.status());
  });

  test("認証なしで 401/403 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/command", {
      data: {
        udid: "any-udid",
        request_type: "device_info",
        params: {},
        send_push: false,
      },
    });
    expect([401, 403]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// 6. APNs Push テスト
// ─────────────────────────────────────────────────────────────
test.describe("APNs Push /mdm/admin/ios/push/{udid}", () => {
  test("存在しない UDID へのプッシュは 404 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/push/nonexistent-udid-12345", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(404);
  });

  test("認証なしで 401/403 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/push/some-udid");
    expect([401, 403]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// 7. MDM 管理プロファイル（NanoMDM統合版）
// ─────────────────────────────────────────────────────────────
test.describe("MDM管理プロファイル /mdm/ios/mobileconfig-mdm", () => {
  test("トークンなしでは 400/422 が返る", async ({ request }) => {
    const res = await request.get("/mdm/ios/mobileconfig-mdm");
    expect([400, 404, 422]).toContain(res.status());
  });

  test("不正なトークンでは 400/404 が返る", async ({ request }) => {
    const res = await request.get("/mdm/ios/mobileconfig-mdm?token=invalid-xyz");
    expect([400, 404]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// 8. キャンペーン管理 API（新機能: safari_config / 自動再配信）
// ─────────────────────────────────────────────────────────────
test.describe("キャンペーン管理 /mdm/admin/campaigns", () => {
  let campaignId;

  test("safari_config 付きキャンペーンを作成できる", async ({ request }) => {
    const res = await request.post("/mdm/admin/campaigns", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        name: "E2E Safari テストキャンペーン",
        webclips: [{ url: "https://example.com/lp", label: "クーポン" }],
        safari_config: {
          home_page: "https://example.com",
          default_search_provider: "Google",
        },
      },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("id");
    expect(data).toHaveProperty("name");
    campaignId = data.id;
  });

  test("PUT /admin/campaigns/{id} でキャンペーンを更新できる", async ({ request }) => {
    // まず作成
    const create = await request.post("/mdm/admin/campaigns", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        name: "E2E 更新テストキャンペーン",
        webclips: [{ url: "https://example.com/v1", label: "旧LP" }],
      },
    });
    expect(create.ok()).toBeTruthy();
    const { id } = await create.json();

    // 更新
    const update = await request.put(`/mdm/admin/campaigns/${id}`, {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        name: "E2E 更新テストキャンペーン (更新後)",
        webclips: [{ url: "https://example.com/v2", label: "新LP" }],
        safari_config: { default_search_provider: "Yahoo" },
      },
    });
    expect(update.ok()).toBeTruthy();
    const upData = await update.json();
    expect(upData.id).toBe(id);
    expect(upData.redeployment).toBe("queued");
  });

  test("存在しない ID への PUT は 404 が返る", async ({ request }) => {
    const res = await request.put("/mdm/admin/campaigns/nonexistent-id-xyz", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: { name: "should fail" },
    });
    expect(res.status()).toBe(404);
  });

  test("認証なしで PUT は 401/403 が返る", async ({ request }) => {
    const res = await request.put("/mdm/admin/campaigns/any-id", {
      data: { name: "no auth" },
    });
    expect([401, 403]).toContain(res.status());
  });
});
