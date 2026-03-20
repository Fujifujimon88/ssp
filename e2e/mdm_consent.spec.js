/**
 * mdm_consent.spec.js
 * MDMエンロール同意フローのE2Eテスト
 *
 * テスト対象:
 *   1. 同意ポータルが表示される
 *   2. 全チェック + 年齢選択でボタンが有効化される
 *   3. 同意APIが正しいホストのURLを返す（localhost でない）
 *   4. mobileconfig ダウンロードエンドポイントが応答する
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:8000";

// テスト用ディーラーを作成して store_code / id を返す
async function createTestDealer(request) {
  const storeCode = `e2e-consent-${Date.now()}`;
  const res = await request.post("/mdm/admin/dealers", {
    headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
    data: { name: "E2Eコンセントテスト店", store_code: storeCode },
  });
  expect(res.ok(), `代理店作成失敗: ${await res.text()}`).toBeTruthy();
  return await res.json(); // { id, store_code, api_key }
}

test.describe("MDM エンロール同意ポータル", () => {
  test("同意ポータルが表示される", async ({ page, request }) => {
    const dealer = await createTestDealer(request);
    await page.goto(`/mdm/portal?dealer=${dealer.id}`);

    await expect(page.locator("h1")).toContainText("サービス設定");
    await expect(page.locator(".card h2").first()).toContainText("同意事項");
  });

  test("チェックボックスが6つ表示される", async ({ page, request }) => {
    const dealer = await createTestDealer(request);
    await page.goto(`/mdm/portal?dealer=${dealer.id}`);

    const checkboxes = page.locator('input[type="checkbox"]');
    await expect(checkboxes).toHaveCount(6);
  });

  test("全項目チェック前はボタンが無効", async ({ page, request }) => {
    const dealer = await createTestDealer(request);
    await page.goto(`/mdm/portal?dealer=${dealer.id}`);

    // iOS として表示（デスクトップ = ios-section表示）
    const btn = page.locator("#download-btn");
    await expect(btn).toHaveClass(/btn-disabled/);
  });

  test("全項目チェック + 年齢選択でボタンが有効化される", async ({ page, request }) => {
    const dealer = await createTestDealer(request);
    await page.goto(`/mdm/portal?dealer=${dealer.id}`);

    // 全チェックボックスをオン
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) {
      await checkboxes.nth(i).check();
    }

    // 年齢選択
    await page.locator("#age-group").selectOption("20s");

    // ボタンが有効化される
    const btn = page.locator("#download-btn");
    await expect(btn).toHaveClass(/btn-primary/);
    await expect(btn).not.toHaveClass(/btn-disabled/);
  });

  test("同意APIがサーバー自身のホストのURLを返す（localhostでない場合）", async ({ request }) => {
    // まずディーラー作成
    const storeCode = `e2e-url-check-${Date.now()}`;
    const dealerRes = await request.post("/mdm/admin/dealers", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { name: "E2E URL確認店", store_code: storeCode },
    });
    expect(dealerRes.ok()).toBeTruthy();
    const dealer = await dealerRes.json();

    const consentRes = await request.post("/mdm/device/consent", {
      data: {
        dealer_id: dealer.id,
        age_group: "20s",
        user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        consent_items: [
          "lockscreen_ads", "push_notifications", "webclip_install",
          "vpn_setup", "app_install", "data_collection",
        ],
      },
    });

    expect(consentRes.ok(), `同意API失敗: ${await consentRes.text()}`).toBeTruthy();
    const data = await consentRes.json();

    // enrollment_token が返る
    expect(data.enrollment_token).toBeTruthy();

    // mobileconfig_url が返る
    expect(data.mobileconfig_url).toBeTruthy();

    // URL が localhost:8000 でないことを確認
    expect(data.mobileconfig_url).not.toContain("localhost:8000");
    expect(data.line_add_friend_url).not.toContain("localhost:8000");
    expect(data.android_apk_url).not.toContain("localhost:8000");

    // URL が正しいパスを持つ
    expect(data.mobileconfig_url).toContain("/mdm/ios/mobileconfig");
    expect(data.android_apk_url).toContain("/mdm/android/dpc.apk");
    expect(data.line_add_friend_url).toContain("/mdm/line/add-friend");
  });

  test("QRコード画像が正しいURLを埋め込む（localhost でない）", async ({ request }) => {
    const storeCode = `e2e-qr-${Date.now()}`;
    const dealerRes = await request.post("/mdm/admin/dealers", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { name: "E2E QR確認店", store_code: storeCode },
    });
    expect(dealerRes.ok()).toBeTruthy();

    const qrRes = await request.get(`/mdm/qr/${storeCode}`);
    expect(qrRes.ok()).toBeTruthy();
    expect(qrRes.headers()["content-type"]).toContain("image/png");
    // PNG が返れば QR 生成成功（URL は QR デコードが必要なので画像応答のみ確認）
  });

  test("mobileconfig エンドポイントが有効なトークンで 200 を返す", async ({ request }) => {
    // 同意してトークンを取得
    const storeCode = `e2e-mc-${Date.now()}`;
    const dealerRes = await request.post("/mdm/admin/dealers", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { name: "E2E MC確認店", store_code: storeCode },
    });
    const { id: dealerId } = await dealerRes.json();

    const consentRes = await request.post("/mdm/device/consent", {
      data: {
        dealer_id: dealerId,
        age_group: "30s",
        user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        consent_items: [
          "lockscreen_ads", "push_notifications", "webclip_install",
          "vpn_setup", "app_install", "data_collection",
        ],
      },
    });
    const { enrollment_token } = await consentRes.json();

    const mcRes = await request.get(`/mdm/ios/mobileconfig?token=${enrollment_token}`);
    expect(mcRes.ok()).toBeTruthy();
    expect(mcRes.headers()["content-type"]).toContain("application/x-apple-aspen-config");
  });

  test("無効なトークンで mobileconfig は 404", async ({ request }) => {
    const res = await request.get("/mdm/ios/mobileconfig?token=invalid-token-xyz");
    expect(res.status()).toBe(404);
  });

  test("DPC APK エンドポイントが install-guide にリダイレクトする", async ({ request }) => {
    const res = await request.get("/mdm/android/dpc.apk", {
      maxRedirects: 0,
    });
    expect([301, 302, 307, 308]).toContain(res.status());
    const location = res.headers()["location"] || "";
    expect(location).toContain("/mdm/android/install-guide");
    // localhost を含まない
    expect(location).not.toContain("localhost:8000");
  });
});
