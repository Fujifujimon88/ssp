/**
 * publisher.spec.js
 * パブリッシャー認証・情報取得のE2Eテスト（JWT API経由）
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet } = require("./helpers/auth");

test.describe("パブリッシャー認証", () => {
  test("正しい認証情報でJWTトークンを取得できる", async ({ request }) => {
    const { domain } = loadAuth();
    const password =
      process.env.TEST_PASSWORD || "e2eTestPass123";

    const res = await request.post("/auth/token", {
      form: { username: domain, password },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("access_token");
    expect(data.token_type).toBe("bearer");
  });

  test("誤ったパスワードでは401が返る", async ({ request }) => {
    const { domain } = loadAuth();
    const res = await request.post("/auth/token", {
      form: { username: domain, password: "wrongpassword" },
    });
    expect(res.status()).toBe(401);
  });

  test("存在しないドメインでは401が返る", async ({ request }) => {
    const res = await request.post("/auth/token", {
      form: {
        username: "nonexistent-domain-xyz.example.com",
        password: "anypassword",
      },
    });
    expect(res.status()).toBe(401);
  });

  test("JWTで /api/publishers/me を取得できる", async ({ request }) => {
    const res = await apiGet(request, "/api/publishers/me");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("id");
    expect(data).toHaveProperty("domain");
    expect(data).toHaveProperty("api_key");
  });

  test("トークンなしで /api/publishers/me は401が返る", async ({
    request,
  }) => {
    const res = await request.get("/api/publishers/me");
    expect(res.status()).toBe(401);
  });
});

test.describe("パブリッシャー登録（UIモーダル）", () => {
  test("重複ドメインで登録するとエラーメッセージが出る", async ({ page }) => {
    const { domain } = loadAuth();
    await page.goto("/admin");
    await page.getByText("+ パブリッシャー登録").first().click();

    const form = page.locator("#register-form");
    await form.locator('[name="name"]').fill("Duplicate Test");
    await form.locator('[name="domain"]').fill(domain); // 既存ドメイン
    await form.locator('[name="email"]').fill("dup@example.com");
    await form.locator('[name="password"]').fill("anypassword");
    await form.locator('[type="submit"]').click();

    // エラーメッセージが表示される
    await expect(page.locator("#register-result")).toBeVisible();
    await expect(page.locator("#register-result")).toContainText(
      /already registered|エラー|Domain/i,
      { timeout: 5000 }
    );
  });

  test("新しいドメインでパブリッシャーを登録できる", async ({ page }) => {
    const uniqueDomain = `e2e-new-${Date.now()}.example.com`;
    await page.goto("/admin");
    await page.getByText("+ パブリッシャー登録").first().click();

    const form = page.locator("#register-form");
    await form.locator('[name="name"]').fill("New E2E Publisher");
    await form.locator('[name="domain"]').fill(uniqueDomain);
    await form.locator('[name="email"]').fill(`e2e-${Date.now()}@example.com`);
    await form.locator('[name="floor"]').fill("0.8");
    await form.locator('[name="password"]').fill("NewPass123");
    await form.locator('[type="submit"]').click();

    // 登録完了メッセージ
    await expect(page.locator("#register-result")).toContainText(
      /登録完了|API Key/i,
      { timeout: 5000 }
    );
  });
});
