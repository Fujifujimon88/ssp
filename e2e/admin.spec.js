/**
 * admin.spec.js
 * 管理機能（パブリッシャーステータス変更・レポート）のE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, adminGet, adminPut } = require("./helpers/auth");

test.describe("パブリッシャーステータス管理API", () => {
  test("pending → active に変更できる", async ({ request }) => {
    const { publisherId } = loadAuth();
    const res = await adminPut(request, `/api/admin/publishers/${publisherId}/status?status=active`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("active");
  });

  test("active → suspended に変更できる", async ({ request }) => {
    const { publisherId } = loadAuth();
    const res = await adminPut(request, `/api/admin/publishers/${publisherId}/status?status=suspended`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("suspended");
  });

  test("suspended → active に戻せる", async ({ request }) => {
    const { publisherId } = loadAuth();
    const res = await adminPut(request, `/api/admin/publishers/${publisherId}/status?status=active`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("active");
  });

  test("不正なステータスは 400 が返る", async ({ request }) => {
    const { publisherId } = loadAuth();
    const res = await adminPut(request, `/api/admin/publishers/${publisherId}/status?status=invalid`);
    expect(res.status()).toBe(400);
  });

  test("存在しないパブリッシャーは 404 が返る", async ({ request }) => {
    const res = await adminPut(request, `/api/admin/publishers/nonexistent-id/status?status=active`);
    expect(res.status()).toBe(404);
  });
});

test.describe("期間レポートAPI", () => {
  test("/api/reports/range が配列を返す", async ({ request }) => {
    const res = await apiGet(request, "/api/reports/range?days=7");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
    expect(data).toHaveLength(7);
  });

  test("days=14 で 14件返る", async ({ request }) => {
    const res = await apiGet(request, "/api/reports/range?days=14");
    const data = await res.json();
    expect(data).toHaveLength(14);
  });

  test("各レコードに必要なフィールドがある", async ({ request }) => {
    const res = await apiGet(request, "/api/reports/range?days=3");
    const data = await res.json();
    for (const r of data) {
      expect(r).toHaveProperty("date");
      expect(r).toHaveProperty("impressions");
      expect(r).toHaveProperty("fill_rate");
      expect(r).toHaveProperty("revenue_usd");
      expect(r).toHaveProperty("ecpm");
    }
  });

  test("トークンなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/api/reports/range?days=7");
    expect(res.status()).toBe(401);
  });
});

test.describe("レポートセクション（UI）", () => {
  test("レポートセクションに切り替えられる", async ({ page }) => {
    const { token } = loadAuth();
    await page.goto("/login");
    await page.evaluate((t) => localStorage.setItem("ssp_token", t), token);
    await page.goto("/dashboard");

    await page.locator("#nav-report").click();
    await expect(page.locator("#section-report")).toHaveClass(/active/);
    await expect(page.locator("#report-days")).toBeVisible();
  });
});
