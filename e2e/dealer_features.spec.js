/**
 * dealer_features.spec.js — 代理店向け機能 E2E テスト
 *
 * テスト対象:
 *   GET  /mdm/dealer/stats/today
 *   POST /mdm/dealer/push
 *   GET  /mdm/dealer/webclips
 *   PUT  /mdm/dealer/webclips
 *   GET  /mdm/dealer/portal
 */
const { test, expect } = require("@playwright/test");
const { adminGet } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const STORE_CODE = `e2e-dealer-${Date.now()}`;

let dealerApiKey = null;

test.describe.configure({ mode: "serial" });

test.describe("代理店機能 E2E", () => {
  test.beforeAll(async ({ request }) => {
    // テスト用ディーラーを作成
    const res = await request.post("/mdm/admin/dealers", {
      data: {
        name: "E2Eテスト店舗",
        store_code: STORE_CODE,
        address: "東京都テスト区1-1-1",
      },
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    dealerApiKey = d.api_key;
    expect(dealerApiKey).toBeTruthy();
  });

  test("dealer/stats/today 正常系 — impressionsフィールドが返る", async ({ request }) => {
    const res = await request.get(
      `/mdm/dealer/stats/today?api_key=${dealerApiKey}`
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("impressions");
    expect(d).toHaveProperty("clicks");
    expect(d).toHaveProperty("ctr");
    expect(d).toHaveProperty("today_cpm_revenue_jpy");
    expect(d).toHaveProperty("device_count");
    expect(d).toHaveProperty("month_revenue_jpy");
  });

  test("dealer/stats/today 認証エラー — 401が返る", async ({ request }) => {
    const res = await request.get(
      "/mdm/dealer/stats/today?api_key=invalid-key-000"
    );
    expect(res.status()).toBe(401);
  });

  test("dealer/webclips GET — webclipsフィールドが返る", async ({ request }) => {
    const res = await request.get(
      `/mdm/dealer/webclips?api_key=${dealerApiKey}`
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("campaign_id");
    expect(d).toHaveProperty("webclips");
    expect(Array.isArray(d.webclips)).toBeTruthy();
  });

  test("dealer/webclips PUT — WebClipを更新できる", async ({ request }) => {
    const webclips = [
      { label: "公式サイト", url: "https://example.com", icon_url: null },
    ];
    const res = await request.put(
      `/mdm/dealer/webclips?api_key=${dealerApiKey}`,
      { data: { webclips } }
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.ok).toBe(true);
    expect(d.webclip_count).toBe(1);
  });

  test("dealer/push 通知送信 — レスポンス正常", async ({ request }) => {
    const res = await request.post(
      `/mdm/dealer/push?api_key=${dealerApiKey}`,
      {
        data: {
          title: "E2Eテスト通知",
          body: "これはテストです",
          url: "https://example.com",
        },
      }
    );
    // デバイスなしの場合でも 200 が返る
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("ok");
    expect(d).toHaveProperty("sent");
    expect(d).toHaveProperty("remaining_this_month");
  });

  test("dealer portal ページが表示される", async ({ page }) => {
    await page.goto(`/mdm/dealer/portal?api_key=${dealerApiKey}`);
    await expect(page).toHaveTitle(/代理店ポータル/);
    // 本日の成果セクション
    await expect(page.locator("#today-stats")).toBeVisible();
    // プッシュ通知セクション
    await expect(page.locator("#push-title")).toBeVisible();
    // WebClipテーブル
    await expect(page.locator("#webclip-table")).toBeVisible();
  });
});
