/**
 * mdm_dashboard.spec.js
 * MDM管理ダッシュボード・アフィリエイトレポートAPI・代理店ポータルのE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { adminGet } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

// ─────────────────────────────────────────────────────────────
// MDM管理ダッシュボード（/mdm/admin/dashboard）
// ─────────────────────────────────────────────────────────────

test.describe("MDM管理ダッシュボード /mdm/admin/dashboard", () => {
  test("認証なしでアクセスすると 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/dashboard");
    expect(res.status()).toBe(401);
  });

  test("有効な管理者キーでダッシュボードが表示される", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "X-Admin-Key": ADMIN_KEY });
    await page.goto("/mdm/admin/dashboard");
    await expect(page.locator("h1")).toContainText("MDM管理ダッシュボード");
  });

  test("KPIカードが表示される（端末・代理店・収益など）", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "X-Admin-Key": ADMIN_KEY });
    await page.goto("/mdm/admin/dashboard");
    // KPIグリッド内に .card 要素が1件以上存在することを確認
    const cards = page.locator(".main > .grid > .card");
    await expect(cards.first()).toBeVisible();
    // 8枚のKPIカードが存在する（総端末、Android、iOS、代理店、案件、クリック、CV、収益）
    await expect(cards).toHaveCount(8);
  });

  test("代理店 Top 5 テーブルセクションが表示される", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "X-Admin-Key": ADMIN_KEY });
    await page.goto("/mdm/admin/dashboard");
    await expect(page.locator(".section").first()).toBeVisible();
    await expect(page.locator(".section h2").nth(1)).toContainText("代理店 Top 5");
  });

  test("アフィリエイト案件 Top 5 テーブルセクションが表示される", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "X-Admin-Key": ADMIN_KEY });
    await page.goto("/mdm/admin/dashboard");
    const sections = page.locator(".section h2");
    await expect(sections.nth(2)).toContainText("アフィリエイト案件 Top 5");
  });

  test("主要APIエンドポイント一覧セクションが表示される", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "X-Admin-Key": ADMIN_KEY });
    await page.goto("/mdm/admin/dashboard");
    const sections = page.locator(".section h2");
    await expect(sections.nth(3)).toContainText("主要APIエンドポイント");
  });
});

// ─────────────────────────────────────────────────────────────
// アフィリエイト収益レポートAPI（/mdm/admin/affiliate/report）
// ─────────────────────────────────────────────────────────────

test.describe("アフィリエイトレポートAPI /mdm/admin/affiliate/report", () => {
  test("有効なキーで 200 + 必要フィールドが返る", async ({ request }) => {
    const res = await adminGet(request, "/mdm/admin/affiliate/report");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("period");
    expect(data).toHaveProperty("total_revenue_jpy");
  });

  test("year=2026&month=3 を指定すると period が '2026-03' になる", async ({ request }) => {
    const res = await adminGet(request, "/mdm/admin/affiliate/report?year=2026&month=3");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.period).toBe("2026-03");
  });

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/report");
    expect(res.status()).toBe(401);
  });
});

// ─────────────────────────────────────────────────────────────
// CV一覧API（/mdm/admin/affiliate/conversions）
// ─────────────────────────────────────────────────────────────

test.describe("CV一覧API /mdm/admin/affiliate/conversions", () => {
  test("有効なキーで 200 + 配列が返る", async ({ request }) => {
    const res = await adminGet(request, "/mdm/admin/affiliate/conversions");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/conversions");
    expect(res.status()).toBe(401);
  });
});

// ─────────────────────────────────────────────────────────────
// 代理店ポータル（/mdm/dealer/portal）
// ─────────────────────────────────────────────────────────────

test.describe("代理店ポータル /mdm/dealer/portal", () => {
  test("無効な api_key で 403 が返る", async ({ request }) => {
    const res = await request.get("/mdm/dealer/portal?api_key=INVALID_KEY");
    expect(res.status()).toBe(403);
  });
});
