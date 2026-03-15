/**
 * slots_advanced.spec.js
 * スロット削除・日次レポート・レポートUI日数切り替えのE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, apiPost } = require("./helpers/auth");

async function createSlot(request, name) {
  const { publisherId, token } = loadAuth();
  return request.post("/api/slots", {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      publisher_id: publisherId,
      name,
      width: 300,
      height: 250,
      floor_price: 1.0,
    },
  });
}

test.describe("DELETE /api/slots/{id} スロット停止", () => {
  test("スロットを作成して停止できる", async ({ request }) => {
    const { token } = loadAuth();
    const slotName = `削除テストスロット_${Date.now()}`;

    const createRes = await createSlot(request, slotName);
    expect(createRes.ok()).toBeTruthy();
    const slot = await createRes.json();
    const slotId = slot.id;

    const deleteRes = await request.delete(`/api/slots/${slotId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(deleteRes.ok()).toBeTruthy();
  });

  test("停止後はスロット一覧に含まれない", async ({ request }) => {
    const { token } = loadAuth();
    const slotName = `停止確認スロット_${Date.now()}`;

    const createRes = await createSlot(request, slotName);
    expect(createRes.ok()).toBeTruthy();
    const slot = await createRes.json();
    const slotId = slot.id;

    const deleteRes = await request.delete(`/api/slots/${slotId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(deleteRes.ok()).toBeTruthy();

    // スロット一覧でそのIDが active=false になっていることを確認
    const listRes = await apiGet(request, "/api/slots");
    const slots = await listRes.json();
    const found = slots.find((s) => s.id === slotId);
    expect(found).toBeDefined();
    expect(found.active).toBe(false);
  });

  test("存在しないスロットの削除は 404", async ({ request }) => {
    const { token } = loadAuth();
    const res = await request.delete("/api/slots/nonexistent-slot-id", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status()).toBe(404);
  });
});

test.describe("GET /api/reports/daily 日次レポート", () => {
  test("本日のレポートが取得できる", async ({ request }) => {
    const res = await apiGet(request, "/api/reports/daily");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("date");
    expect(data).toHaveProperty("impressions");
    expect(data).toHaveProperty("fill_rate");
    expect(data).toHaveProperty("revenue_usd");
    expect(data).toHaveProperty("ecpm");
  });

  test("date パラメータで特定日のレポートが取得できる", async ({ request }) => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const dateStr = yesterday.toISOString().split("T")[0];

    const res = await apiGet(request, `/api/reports/daily?date_str=${dateStr}`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.date).toBe(dateStr);
  });

  test("トークンなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/api/reports/daily");
    expect(res.status()).toBe(401);
  });
});

test.describe("レポートUI 日数切り替え", () => {
  async function loginAndGo(page, path = "/dashboard") {
    const { token } = loadAuth();
    await page.goto("/login");
    await page.evaluate((t) => localStorage.setItem("ssp_token", t), token);
    await page.goto(path);
  }

  test("14日に切り替えるとデータが更新される", async ({ page }) => {
    await loginAndGo(page);
    await page.locator("#nav-report").click();
    await expect(page.locator("#section-report")).toHaveClass(/active/);

    await page.locator("#report-days").selectOption("14");
    await expect(page.locator("#report-tbody")).toBeVisible({ timeout: 8000 });
  });

  test("30日に切り替えるとデータが更新される", async ({ page }) => {
    await loginAndGo(page);
    await page.locator("#nav-report").click();

    await page.locator("#report-days").selectOption("30");
    await expect(page.locator("#report-tbody")).toBeVisible({ timeout: 8000 });
  });
});
