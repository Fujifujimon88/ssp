/**
 * agency_management.spec.js — 代理店組織管理 E2E テスト
 *
 * テスト対象:
 *   POST /mdm/admin/agencies              — 代理店登録
 *   GET  /mdm/admin/agencies              — 代理店一覧
 *   GET  /mdm/admin/agencies-with-stores  — 代理店+店舗一覧
 *   POST /mdm/admin/agencies/{id}/stores  — 店舗追加
 *   UI   /agencies                        — 代理店組織管理画面
 */
const { test, expect } = require("@playwright/test");
const { setAdminKeyInBrowser } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const ADMIN_HEADERS = { "X-Admin-Key": ADMIN_KEY };
const UID = Date.now();

let agencyId = null;
let agencyName = `E2E代理店-${UID}`;

test.describe.configure({ mode: "serial" });

// ── API テスト ──────────────────────────────────────────────────

test.describe("代理店管理 API", () => {
  test.beforeAll(async ({ request }) => {
    // テスト用代理店を作成
    const res = await request.post("/mdm/admin/agencies", {
      data: { name: agencyName, contact_email: `e2e-${UID}@example.com` },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    agencyId = d.id;
    expect(agencyId).toBeTruthy();
    expect(d.api_key).toBeTruthy();
  });

  test("代理店登録 — 正常系: id/name/api_key が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      data: { name: `E2E追加代理店-${UID}`, contact_email: "extra@example.com" },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("id");
    expect(d).toHaveProperty("name");
    expect(d).toHaveProperty("api_key");
  });

  test("代理店登録 — name が空 → 400", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      data: { name: "" },
      headers: ADMIN_HEADERS,
    });
    expect(res.status()).toBe(400);
  });

  test("代理店登録 — 管理者キーなし → 401 または 403", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      data: { name: "不正アクセス" },
    });
    expect(res.status()).toBeGreaterThanOrEqual(401);
    expect(res.status()).toBeLessThanOrEqual(403);
  });

  test("代理店一覧 GET — 作成した代理店が含まれる", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies", { headers: ADMIN_HEADERS });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("agencies");
    expect(Array.isArray(data.agencies)).toBeTruthy();
    const ids = data.agencies.map((a) => a.id);
    expect(ids).toContain(agencyId);
  });

  test("代理店一覧 GET — 管理者キーなし → 401 または 403", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies");
    expect(res.status()).toBeGreaterThanOrEqual(401);
  });

  test("agencies-with-stores — stores フィールドを持つ", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies-with-stores", {
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("agencies");
    const found = data.agencies.find((a) => a.id === agencyId);
    expect(found).toBeTruthy();
    expect(Array.isArray(found.stores)).toBeTruthy();
  });

  test("店舗追加 — 1店舗目: store_number=1", async ({ request }) => {
    const res = await request.post(`/mdm/admin/agencies/${agencyId}/stores`, {
      data: {
        name: `E2E店舗1-${UID}`,
        store_code: `E2E-ST1-${UID}`,
        address: "東京都テスト区1-1-1",
      },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.store_number).toBe(1);
    expect(d.agency_id).toBe(agencyId);
    expect(d).toHaveProperty("api_key");
  });

  test("店舗追加 — 2店舗目: store_number=2 に自動インクリメント", async ({ request }) => {
    const res = await request.post(`/mdm/admin/agencies/${agencyId}/stores`, {
      data: {
        name: `E2E店舗2-${UID}`,
        store_code: `E2E-ST2-${UID}`,
      },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.store_number).toBe(2);
  });

  test("agencies-with-stores — 追加後に stores が2件含まれる", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies-with-stores", {
      headers: ADMIN_HEADERS,
    });
    const data = await res.json();
    const found = data.agencies.find((a) => a.id === agencyId);
    expect(found.stores.length).toBeGreaterThanOrEqual(2);
  });

  test("存在しない代理店への店舗追加 → 404", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies/99999/stores", {
      data: { name: "幽霊店舗", store_code: "GHOST-001" },
      headers: ADMIN_HEADERS,
    });
    expect(res.status()).toBe(404);
  });
});

// ── UI テスト ──────────────────────────────────────────────────

test.describe("代理店組織管理 UI (/agencies)", () => {
  test("サイドバーに「代理店組織管理」リンクが表示される", async ({ page }) => {
    await page.goto("/admin");
    await setAdminKeyInBrowser(page);
    await expect(page.locator("nav").getByText("代理店組織管理")).toBeVisible();
  });

  test("/agencies ページが表示される", async ({ page }) => {
    await page.goto("/agencies");
    await setAdminKeyInBrowser(page);
    await expect(page.locator("section#agencies")).toBeVisible();
    await expect(page.getByRole("heading", { name: "代理店組織管理" })).toBeVisible();
  });

  test("「+ 代理店登録」ボタンが存在する", async ({ page }) => {
    await page.goto("/agencies");
    await setAdminKeyInBrowser(page);
    await expect(page.getByRole("button", { name: /代理店登録/ })).toBeVisible();
  });

  test("代理店登録モーダルが開閉できる", async ({ page }) => {
    await page.goto("/agencies");
    await setAdminKeyInBrowser(page);
    await page.getByRole("button", { name: /代理店登録/ }).click();
    await expect(page.locator("#agency-modal")).toHaveClass(/open/);
    await page.locator("#agency-modal .modal-close").click();
    await expect(page.locator("#agency-modal")).not.toHaveClass(/open/);
  });

  test("代理店をUIから登録すると一覧に表示される", async ({ page }) => {
    await page.goto("/agencies");
    await setAdminKeyInBrowser(page);

    const newName = `UI代理店-${Date.now()}`;
    await page.getByRole("button", { name: /代理店登録/ }).click();
    await page.locator("#agency-form [name=name]").fill(newName);
    await page.locator("#agency-form [name=contact_email]").fill("ui-test@example.com");
    await page.locator("#agency-form button[type=submit]").click();

    // 成功メッセージ確認
    await expect(page.locator("#agency-result")).toContainText("登録完了");

    // 一覧に表示される
    await expect(page.locator("#agencies-list")).toContainText(newName);
  });
});
