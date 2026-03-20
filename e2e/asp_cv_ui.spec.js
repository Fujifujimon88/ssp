/**
 * asp_cv_ui.spec.js
 * ASP CVレポート・ポイント付与履歴・Androidデバイスuser_token表示 のE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { setAdminKeyInBrowser } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

test.beforeEach(async ({ page }) => {
  await page.goto("/admin");
  await setAdminKeyInBrowser(page);
  await page.waitForLoadState("networkidle");
});

// ─────────────────────────────────────────────
// ASP CV レポート — サイドバーナビ
// ─────────────────────────────────────────────
test.describe("ASP CVレポート — ナビ・セクション表示", () => {
  test("サイドバーに「ASP CVレポート」リンクが表示される", async ({ page }) => {
    await expect(
      page.locator("nav").getByText("ASP CVレポート", { exact: false })
    ).toBeVisible();
  });

  test("ナビリンクをクリックするとセクションが表示される", async ({ page }) => {
    await page.locator("nav").getByText("ASP CVレポート", { exact: false }).click();
    await expect(page.locator("#asp-cv-report")).toBeVisible();
  });

  test("セクションに「🔄 更新」ボタンが表示される", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("asp-cv-report"); });
    await page.waitForTimeout(300);
    await expect(
      page.locator("#asp-cv-report").getByRole("button", { name: /更新/ })
    ).toBeVisible();
  });

  test("ページ読み込み時にCVデータが読み込まれる（テーブルまたは空欄が表示）", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("asp-cv-report"); });
    await expect(page.locator("#asp-cv-loading")).toBeHidden({ timeout: 8000 });
    const tableVisible = await page.locator("#asp-cv-table").isVisible();
    const emptyVisible = await page.locator("#asp-cv-empty").isVisible();
    expect(tableVisible || emptyVisible).toBeTruthy();
  });

  test("テーブルヘッダーに ASP / user_token / action_id / ステータス が表示される", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("asp-cv-report"); });
    await expect(page.locator("#asp-cv-loading")).toBeHidden({ timeout: 8000 });
    if (await page.locator("#asp-cv-table").isVisible()) {
      const header = page.locator("#asp-cv-table thead tr");
      await expect(header).toContainText("ASP");
      await expect(header).toContainText("user_token");
      await expect(header).toContainText("action_id");
      await expect(header).toContainText("ステータス");
    }
  });

  test("「🔄 更新」クリックでAPIが再リクエストされる", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("asp-cv-report"); });
    await expect(page.locator("#asp-cv-loading")).toBeHidden({ timeout: 8000 });
    let requestCount = 0;
    page.on("request", req => {
      if (req.url().includes("/mdm/admin/affiliate/conversions")) requestCount++;
    });
    await page.locator("#asp-cv-report").getByRole("button", { name: /更新/ }).click();
    await page.waitForTimeout(1000);
    expect(requestCount).toBeGreaterThanOrEqual(1);
  });
});

// ─────────────────────────────────────────────
// ポイント付与履歴 — サイドバーナビ
// ─────────────────────────────────────────────
test.describe("ポイント付与履歴 — ナビ・セクション表示", () => {
  test("サイドバーに「ポイント付与履歴」リンクが表示される", async ({ page }) => {
    await expect(
      page.locator("nav").getByText("ポイント付与履歴", { exact: false })
    ).toBeVisible();
  });

  test("ナビリンクをクリックするとセクションが表示される", async ({ page }) => {
    await page.locator("nav").getByText("ポイント付与履歴", { exact: false }).click();
    await expect(page.locator("#affiliate-points")).toBeVisible();
  });

  test("セクションに「🔄 更新」ボタンとフィルタ入力欄が表示される", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("affiliate-points"); });
    await page.waitForTimeout(300);
    const section = page.locator("#affiliate-points");
    await expect(section.getByRole("button", { name: /更新/ })).toBeVisible();
    await expect(section.getByPlaceholder(/user_token.*フィルタ/)).toBeVisible();
  });

  test("ページ読み込み時にデータが読み込まれる（テーブルまたは空欄が表示）", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("affiliate-points"); });
    await expect(page.locator("#points-loading")).toBeHidden({ timeout: 8000 });
    const tableVisible = await page.locator("#points-table").isVisible();
    const emptyVisible = await page.locator("#points-empty").isVisible();
    expect(tableVisible || emptyVisible).toBeTruthy();
  });

  test("テーブルヘッダーに user_token / 付与ポイント / conversion_id が含まれる", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("affiliate-points"); });
    await expect(page.locator("#points-loading")).toBeHidden({ timeout: 8000 });
    if (await page.locator("#points-table").isVisible()) {
      const header = page.locator("#points-table thead tr");
      await expect(header).toContainText("user_token");
      await expect(header).toContainText("付与ポイント");
      await expect(header).toContainText("conversion_id");
    }
  });

  test("user_token フィルタにEnterを押すとAPIが再リクエストされる", async ({ page }) => {
    await page.evaluate(() => { if (typeof showSection === "function") showSection("affiliate-points"); });
    await expect(page.locator("#points-loading")).toBeHidden({ timeout: 8000 });
    let requestFired = false;
    page.on("request", req => {
      if (req.url().includes("/mdm/admin/affiliate/points")) requestFired = true;
    });
    await page.locator("#points-filter-token").fill("UTtesttoken00");
    await page.locator("#points-filter-token").press("Enter");
    await page.waitForTimeout(1000);
    expect(requestFired).toBeTruthy();
  });
});

// ─────────────────────────────────────────────
// Androidデバイス — user_token カラム
// ─────────────────────────────────────────────
test.describe("Androidデバイス一覧 — user_token カラム", () => {
  test("デバイス管理セクションにuser_tokenカラムヘッダーが表示される", async ({ page }) => {
    await page.evaluate(() => {
      if (typeof showSection === "function") showSection("devices");
      if (typeof loadAndroidDevices === "function") loadAndroidDevices();
    });
    // テーブルまたは空欄が出るまで待つ（ローディングが消える）
    await page.waitForFunction(() => {
      const loading = document.getElementById("android-loading");
      return loading && loading.style.display === "none" || loading && loading.textContent !== "読み込み中...";
    }, { timeout: 10000 }).catch(() => {});
    // DOMにヘッダーが存在すること（テーブルが0件でもheadはある）
    const headerText = await page.locator("#android-table thead").textContent();
    expect(headerText).toContain("user_token");
  });
});

// ─────────────────────────────────────────────
// API直接テスト
// ─────────────────────────────────────────────
test.describe("新規APIエンドポイント — 認証テスト", () => {
  test("/mdm/admin/affiliate/conversions — 認証なしで401", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/conversions");
    expect(res.status()).toBe(401);
  });

  test("/mdm/admin/affiliate/conversions — 管理キーで200", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/conversions", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("/mdm/admin/affiliate/points — 認証なしで401", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/points");
    expect(res.status()).toBe(401);
  });

  test("/mdm/admin/affiliate/points — 管理キーで200", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/points", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("/mdm/admin/affiliate/points — user_tokenクエリフィルタが動作する", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/points?user_token=UTnonexistent00", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBe(0); // 存在しないtokenなので0件
  });

  test("/mdm/admin/android/devices — user_tokenフィールドが返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/android/devices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
    if (data.length > 0) {
      // user_token キーが存在すること（値はnullでも可）
      expect(Object.prototype.hasOwnProperty.call(data[0], "user_token")).toBeTruthy();
      expect(Object.prototype.hasOwnProperty.call(data[0], "enrollment_token")).toBeTruthy();
    }
  });
});
