/**
 * login.spec.js
 * ログインページのE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { loadAuth } = require("./helpers/auth");

test.describe("ログインページ", () => {
  test.beforeEach(async ({ page }) => {
    // localStorage を空にして未ログイン状態にする
    await page.goto("/login");
    await page.evaluate(() => localStorage.removeItem("ssp_token"));
    await page.reload();
  });

  test("ログインページが表示される", async ({ page }) => {
    await expect(page).toHaveTitle(/ログイン.*SSP Platform/);
    await expect(page.locator("h1")).toContainText("SSP Platform");
    await expect(page.locator("#domain")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("誤った認証情報でエラーメッセージが出る", async ({ page }) => {
    await page.locator("#domain").fill("nonexistent.example.com");
    await page.locator("#password").fill("wrongpassword");
    await page.locator('button[type="submit"]').click();
    await expect(page.locator("#error-msg")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#error-msg")).not.toBeEmpty();
  });

  test("正しい認証情報でダッシュボードにリダイレクトされる", async ({
    page,
  }) => {
    const { domain } = loadAuth();
    const password = process.env.TEST_PASSWORD || "e2eTestPass123";

    await page.locator("#domain").fill(domain);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();

    await page.waitForURL(/\/dashboard/, { timeout: 10000 });
    expect(page.url()).toContain("/dashboard");
  });

  test("ログイン後は ssp_token が localStorage に保存される", async ({
    page,
  }) => {
    const { domain } = loadAuth();
    const password = process.env.TEST_PASSWORD || "e2eTestPass123";

    await page.locator("#domain").fill(domain);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();

    await page.waitForURL(/\/dashboard/);
    const token = await page.evaluate(() => localStorage.getItem("ssp_token"));
    expect(token).toBeTruthy();
  });
});
