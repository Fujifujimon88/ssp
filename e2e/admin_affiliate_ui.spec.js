/**
 * admin_affiliate_ui.spec.js
 * アフィリエイトキャンペーン管理UI のE2Eテスト
 * 対象: /admin#affiliate-campaigns セクション
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:8000";

// 各テスト前に管理者キーをlocalStorageにセットして/adminへ遷移し、affiliate-campaignsセクションを表示
test.beforeEach(async ({ page }) => {
  await page.goto("/admin");
  await page.evaluate((key) => {
    localStorage.setItem("ssp_admin_key", key);
  }, ADMIN_KEY);
  await page.reload();
  await page.waitForLoadState("networkidle");
  // サイドバーでアフィリエイトキャンペーンセクションに切り替え
  await page.evaluate(() => {
    if (typeof showSection === "function") showSection("affiliate-campaigns");
  });
  await page.waitForTimeout(300);
});

// ─────────────────────────────────────────────────────────────
// セクション表示
// ─────────────────────────────────────────────────────────────
test.describe("アフィリエイトキャンペーン管理 — セクション表示", () => {
  test("ナビに「アフィリエイトキャンペーン」リンクが存在する", async ({ page }) => {
    await expect(
      page.locator("nav").getByText("アフィリエイトキャンペーン", { exact: false })
    ).toBeVisible();
  });

  test("ナビリンクをクリックするとセクションへスクロールする", async ({ page }) => {
    await page.locator("nav").getByText("アフィリエイトキャンペーン", { exact: false }).click();
    await expect(page.locator("#affiliate-campaigns")).toBeVisible();
  });

  test("セクションに「+ 新規登録」ボタンが表示される", async ({ page }) => {
    await expect(
      page.locator("#affiliate-campaigns").getByRole("button", { name: "+ 新規登録" })
    ).toBeVisible();
  });

  test("ページ読み込み時にキャンペーン一覧が読み込まれる（テーブルまたは空欄が表示）", async ({ page }) => {
    // ローディングが消えるのを待つ
    await expect(page.locator("#affiliate-campaigns-loading")).toBeHidden({ timeout: 8000 });
    // テーブルか空欄どちらかが表示されている
    const tableVisible = await page.locator("#affiliate-campaigns-table").isVisible();
    const emptyVisible = await page.locator("#affiliate-campaigns-empty").isVisible();
    expect(tableVisible || emptyVisible).toBeTruthy();
  });
});

// ─────────────────────────────────────────────────────────────
// モーダル開閉
// ─────────────────────────────────────────────────────────────
test.describe("アフィリエイトキャンペーン管理 — モーダル開閉", () => {
  test("「+ 新規登録」でモーダルが開く", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();
    await expect(page.locator("#affiliate-campaign-modal")).toBeVisible();
  });

  test("モーダルの×ボタンで閉じる", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();
    await expect(page.locator("#affiliate-campaign-modal")).toBeVisible();
    await page
      .locator("#affiliate-campaign-modal .modal-close")
      .click();
    await expect(page.locator("#affiliate-campaign-modal")).not.toBeVisible();
  });

  test("モーダルにフォーム項目が揃っている", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();
    const modal = page.locator("#affiliate-campaign-modal");
    await expect(modal.getByPlaceholder(/キャンペーン名/)).toBeVisible();
    await expect(modal.locator("select[name='category']")).toBeVisible();
    await expect(modal.getByPlaceholder(/配信先URL/)).toBeVisible();
    await expect(modal.locator("select[name='reward_type']")).toBeVisible();
    await expect(modal.getByPlaceholder(/CPI単価/)).toBeVisible();
  });
});

// ─────────────────────────────────────────────────────────────
// 新規登録フロー
// ─────────────────────────────────────────────────────────────
test.describe("アフィリエイトキャンペーン管理 — 新規登録", () => {
  test("必要項目を入力して登録すると成功メッセージが表示される", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();

    const modal = page.locator("#affiliate-campaign-modal");
    const uniqueName = `E2E UIテスト ${Date.now()}`;

    await modal.getByPlaceholder(/キャンペーン名/).fill(uniqueName);
    await modal.locator("select[name='category']").selectOption("app");
    await modal.getByPlaceholder(/配信先URL/).fill("https://example.com/lp-e2e");
    await modal.locator("select[name='reward_type']").selectOption("cpi");
    await modal.getByPlaceholder(/CPI単価/).fill("400");

    await modal.getByRole("button", { name: "登録する" }).click();

    // 成功メッセージ
    await expect(page.locator("#affiliate-campaign-result")).toContainText("登録完了", { timeout: 8000 });
  });

  test("登録後に一覧が更新されてキャンペーンが表示される", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();

    const modal = page.locator("#affiliate-campaign-modal");
    const uniqueName = `UI確認テスト ${Date.now()}`;

    await modal.getByPlaceholder(/キャンペーン名/).fill(uniqueName);
    await modal.locator("select[name='category']").selectOption("finance");
    await modal.getByPlaceholder(/配信先URL/).fill("https://example.com/finance-lp");
    await modal.locator("select[name='reward_type']").selectOption("cpl");
    await modal.getByPlaceholder(/CPI単価/).fill("200");

    await modal.getByRole("button", { name: "登録する" }).click();
    await expect(page.locator("#affiliate-campaign-result")).toContainText("登録完了", { timeout: 8000 });

    // 一覧テーブルに登録したキャンペーン名が出現する
    await expect(
      page.locator("#affiliate-campaigns-tbody").getByText(uniqueName)
    ).toBeVisible({ timeout: 8000 });
  });

  test("必須項目（キャンペーン名）が空だと送信できない", async ({ page }) => {
    await page
      .locator("#affiliate-campaigns")
      .getByRole("button", { name: "+ 新規登録" })
      .click();

    const modal = page.locator("#affiliate-campaign-modal");
    // キャンペーン名を空のまま他の必須項目のみ入力
    await modal.getByPlaceholder(/配信先URL/).fill("https://example.com/lp");
    await modal.getByPlaceholder(/CPI単価/).fill("300");

    await modal.getByRole("button", { name: "登録する" }).click();

    // HTML5 required バリデーションによりモーダルは開いたまま・登録完了は表示されない
    await expect(page.locator("#affiliate-campaign-modal")).toBeVisible();
    await expect(page.locator("#affiliate-campaign-result")).not.toContainText("登録完了");
  });
});
