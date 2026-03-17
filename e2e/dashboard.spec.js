/**
 * dashboard.spec.js → /admin エンドポイントのテスト
 * （旧: /dashboard は /admin に移動）
 */
const { test, expect } = require("@playwright/test");
const { adminGet } = require("./helpers/auth");
const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

test.describe("管理画面 /admin", () => {
  test("ページが表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page).toHaveTitle(/管理画面.*SSP Platform/);
  });

  test("ヘッダーに SSP Platform ロゴが表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator("header h1")).toContainText("SSP Platform");
  });

  test("KPIカードが4つ表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator(".kpi-grid").first().locator(".card")).toHaveCount(4);
  });

  test("パブリッシャー一覧セクションが表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator("#publishers")).toBeVisible();
  });

  test("「+ 新規登録」ボタンが表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.getByText("+ パブリッシャー登録").first()).toBeVisible();
  });

  test("「+ 新規登録」クリックで登録モーダルが開く", async ({ page }) => {
    await page.goto("/admin");
    await page.getByText("+ パブリッシャー登録").first().click();
    await expect(page.locator("#register-modal")).toHaveClass(/open/);
  });

  test("登録モーダルの × でモーダルが閉じる", async ({ page }) => {
    await page.goto("/admin");
    await page.getByText("+ パブリッシャー登録").first().click();
    await page.locator(".modal-close").first().click();
    await expect(page.locator("#register-modal")).not.toHaveClass(/open/);
  });

  test("ヘルスチェックAPI接続状態が表示される", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator("#dsp-count")).not.toHaveText("DSP接続中...", {
      timeout: 5000,
    });
  });

  test("/health エンドポイントが ok を返す", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });
});

test.describe("/api/admin/stats", () => {
  test("必要なフィールドが返る", async ({ request }) => {
    const res = await adminGet(request, "/api/admin/stats");
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("impressions");
    expect(d).toHaveProperty("fill_rate");
    expect(d).toHaveProperty("revenue_usd");
    expect(d).toHaveProperty("ecpm");
    expect(d).toHaveProperty("hourly");
    expect(d).toHaveProperty("dsp_breakdown");
  });

  test("hourly は長さ24の配列", async ({ request }) => {
    const res = await adminGet(request, "/api/admin/stats");
    const d = await res.json();
    expect(Array.isArray(d.hourly)).toBeTruthy();
    expect(d.hourly).toHaveLength(24);
  });

  test("キーなしで /api/admin/stats は 401 が返る", async ({ request }) => {
    const res = await request.get("/api/admin/stats");
    expect(res.status()).toBe(401);
  });

  test("KPIが管理画面に表示される", async ({ page }) => {
    await page.goto("/admin");
    await page.evaluate((key) => localStorage.setItem("ssp_admin_key", key), ADMIN_KEY);
    await page.reload();
    await expect(page.locator("#kpi-imp")).not.toHaveText("-", { timeout: 8000 });
    await expect(page.locator("#kpi-rev-val")).not.toHaveText("-", { timeout: 8000 });
  });
});
