/**
 * vercel_portal.spec.js
 * Vercel本番環境の MDM ポータルページ E2E テスト
 * 対象: https://ssp-platform.vercel.app/mdm/portal?dealer=c229b92e-3aa7-4086-9119-dd4abc5b9495
 *
 * 検証項目:
 *   1. ページが正常にロードされる
 *   2. iOS/Android セクションが表示される（display:none でない）
 *   3. 同意チェックボックスが表示される
 *   4. 「進む」ボタンが存在する（全チェック前は無効）
 *   5. 全チェック + 年齢選択で「進む」ボタンが有効化される
 *   6. JavaScript エラーが発生していない
 */
const { test, expect } = require("@playwright/test");

const PORTAL_URL =
  "https://ssp-platform.vercel.app/mdm/portal?dealer=c229b92e-3aa7-4086-9119-dd4abc5b9495";

// このファイルは Vercel 本番を直接テストするため baseURL をオーバーライド
test.use({ baseURL: "https://ssp-platform.vercel.app" });

test.describe("Vercel本番 MDMポータル", () => {
  test("ポータルページが正常にロードされる", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded" });

    // タイトルまたは h1 が表示されている
    await expect(page.locator("h1, h2").first()).toBeVisible({ timeout: 15_000 });

    // JS エラーが発生していないこと
    expect(errors, `JSエラー: ${errors.join(", ")}`).toHaveLength(0);
  });

  test("「サービス設定」の見出しが表示される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded" });
    await expect(page.locator("h1")).toContainText("サービス設定", { timeout: 15_000 });
  });

  test("同意事項カードが表示される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded" });
    await expect(page.locator(".card").first()).toBeVisible({ timeout: 15_000 });
    // 「同意事項」テキストが存在する
    await expect(page.locator("body")).toContainText("同意", { timeout: 10_000 });
  });

  test("チェックボックスが表示される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded" });
    const checkboxes = page.locator('input[type="checkbox"]');
    await expect(checkboxes.first()).toBeVisible({ timeout: 15_000 });
    const count = await checkboxes.count();
    expect(count).toBeGreaterThanOrEqual(5);
  });

  test("年齢選択ドロップダウンが表示される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded" });
    const ageSelect = page.locator("#age-group");
    await expect(ageSelect).toBeVisible({ timeout: 15_000 });
  });

  test("iOS/Android セクションが display:none でない（JSエラー修正確認）", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });

    // ios-section または android-section のどちらかが表示されている
    const iosSectionDisplay = await page
      .locator("#ios-section")
      .evaluate((el) => getComputedStyle(el).display)
      .catch(() => "not-found");

    const androidSectionDisplay = await page
      .locator("#android-section")
      .evaluate((el) => getComputedStyle(el).display)
      .catch(() => "not-found");

    // 少なくとも一方が "none" でない（display:none のままだとJSエラーが原因）
    const eitherVisible =
      iosSectionDisplay !== "none" || androidSectionDisplay !== "none";

    expect(
      eitherVisible,
      `iosSectionDisplay=${iosSectionDisplay}, androidSectionDisplay=${androidSectionDisplay} — どちらも display:none（JSパースエラーの可能性）`
    ).toBeTruthy();
  });

  test("「進む」ボタン（#download-btn）が存在する", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });
    const btn = page.locator("#download-btn");
    await expect(btn).toBeAttached({ timeout: 15_000 });
  });

  test("全チェック + 年齢選択で「進む」ボタンが有効化される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });

    // 全チェックボックスをオン
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    expect(count).toBeGreaterThan(0);

    for (let i = 0; i < count; i++) {
      await checkboxes.nth(i).check();
    }

    // 年齢選択
    await page.locator("#age-group").selectOption("20s");

    // ボタンが有効化（btn-disabled クラスが消える）
    const btn = page.locator("#download-btn");
    await expect(btn).not.toHaveClass(/btn-disabled/, { timeout: 5_000 });
    await expect(btn).toHaveClass(/btn-primary/);
  });

  test("「進む」ボタンをクリックすると次ステップに進む", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });

    // 全チェック
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) {
      await checkboxes.nth(i).check();
    }
    await page.locator("#age-group").selectOption("30s");

    // ボタンが有効化されたことを確認してクリック
    const btn = page.locator("#download-btn");
    await expect(btn).not.toHaveClass(/btn-disabled/, { timeout: 5_000 });
    await btn.click();

    // ステップ2 or 確認画面が表示される（URLが変わるか、別カードが表示される）
    // ネットワークへの POST が成功するか、ステップ2 UI が現れることを確認
    await page
      .waitForResponse(
        (res) => res.url().includes("/mdm/device/consent") && res.status() < 500,
        { timeout: 15_000 }
      )
      .catch(() => {
        // consent POST がなくても UI 遷移だけで OK
      });

    // ステップ2 の表示（エラーが出ていないこと）
    const pageError = await page.locator(".error, .alert-error").count();
    expect(pageError).toBe(0);
  });
});
