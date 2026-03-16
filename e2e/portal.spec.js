/**
 * portal.spec.js
 * パブリッシャーポータル（/dashboard）のE2Eテスト
 * JWT を localStorage にセットしてからテストする
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet } = require("./helpers/auth");

async function loginAndGo(page, path = "/dashboard") {
  const { token } = loadAuth();
  await page.goto("/login");
  await page.evaluate((t) => localStorage.setItem("ssp_token", t), token);
  await page.goto(path);
}

test.describe("パブリッシャーポータル", () => {
  test("未ログインだと /login にリダイレクトされる", async ({ page }) => {
    await page.goto("/login");
    await page.evaluate(() => localStorage.removeItem("ssp_token"));
    await page.goto("/dashboard");
    await page.waitForURL(/\/login/, { timeout: 5000 });
    expect(page.url()).toContain("/login");
  });

  test("ログイン済みでダッシュボードが表示される", async ({ page }) => {
    await loginAndGo(page);
    await expect(page).toHaveTitle(/パブリッシャーポータル/);
    await expect(page.locator("header h1")).toContainText("SSP Platform");
  });

  test("ヘッダーにパブリッシャー名が表示される", async ({ page }) => {
    await loginAndGo(page);
    await expect(page.locator("#pub-name")).not.toHaveText("読み込み中...", {
      timeout: 5000,
    });
  });

  test("KPIカードが4つ表示される", async ({ page }) => {
    await loginAndGo(page);
    await expect(page.locator("#section-overview .kpi-grid .card")).toHaveCount(4);
  });

  test("「スロット管理」ナビに切り替えられる", async ({ page }) => {
    await loginAndGo(page);
    await page.locator("#nav-slots").click();
    await expect(page.locator("#section-slots")).toHaveClass(/active/);
    await expect(page.locator("#slots-table")).toBeVisible();
  });

  test("「DSP連携」セクションに接続中のDSP一覧が表示される", async ({
    page,
  }) => {
    await loginAndGo(page);
    await page.locator("#nav-dsp").click();
    await expect(page.locator("#dsp-connection-list")).not.toContainText(
      "読み込み中...",
      { timeout: 5000 }
    );
  });

  test("「プロフィール」セクションにAPIキーが表示される", async ({ page }) => {
    await loginAndGo(page);
    await page.locator("#nav-profile").click();
    await expect(page.locator("#api-key")).not.toHaveText("—", {
      timeout: 5000,
    });
  });

  test("ログアウトで /login にリダイレクトされる", async ({ page }) => {
    await loginAndGo(page);
    await page.getByText("ログアウト").click();
    await page.waitForURL(/\/login/, { timeout: 5000 });
    expect(page.url()).toContain("/login");
  });
});

test.describe("スロット作成フロー（UI）", () => {
  test("スロット作成モーダルを開いて作成できる", async ({ page }) => {
    await loginAndGo(page, "/dashboard");
    await page.locator("#nav-slots").click();
    await page.getByText("+ 新規スロット").click();
    await expect(page.locator("#modal-create-slot")).toHaveClass(/open/);

    const slotName = `UIテストスロット_${Date.now()}`;
    await page.locator('[name="name"]').fill(slotName);
    // 300x250 はデフォルトでチェック済み
    await page.locator('[name="floor_price"]').fill("0.5");
    await page.locator('button[type="submit"]').click();

    await expect(page.locator("#create-slot-msg")).toContainText(/作成しました/, {
      timeout: 5000,
    });
  });
});

test.describe("DSP統計API", () => {
  test("/api/dsp/stats が配列を返す", async ({ request }) => {
    const res = await apiGet(request, "/api/dsp/stats");
    expect(res.ok()).toBeTruthy();
    const stats = await res.json();
    expect(Array.isArray(stats)).toBeTruthy();
  });

  test("トークンなしで /api/dsp/stats は 401", async ({ request }) => {
    const res = await request.get("/api/dsp/stats");
    expect(res.status()).toBe(401);
  });
});

test.describe("管理画面 /admin", () => {
  test("/admin が表示される（認証不要）", async ({ page }) => {
    await page.goto("/admin");
    await expect(page).toHaveTitle(/管理画面.*SSP Platform/);
  });

  test("/admin にパブリッシャーログインリンクがある", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.getByText("パブリッシャーログイン").first()).toBeVisible();
  });
});
