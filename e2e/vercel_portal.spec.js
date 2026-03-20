/**
 * vercel_portal.spec.js
 * Vercel本番環境の MDM ポータル + mobileconfig E2E テスト
 * 対象: https://ssp-platform.vercel.app/mdm/portal?dealer=c229b92e-3aa7-4086-9119-dd4abc5b9495
 */
const { test, expect } = require("@playwright/test");

const PORTAL_URL =
  "https://ssp-platform.vercel.app/mdm/portal?dealer=c229b92e-3aa7-4086-9119-dd4abc5b9495";
const BASE = "https://ssp-platform.vercel.app";

test.use({ baseURL: BASE });

// ─── ポータル UI ──────────────────────────────────────────────
test.describe("Vercel本番 MDMポータル UI", () => {
  test("JSエラーなくページがロードされる", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toBeVisible({ timeout: 15_000 });
    expect(errors, `JSエラー: ${errors.join(", ")}`).toHaveLength(0);
  });

  test("iOS/Androidセクションが display:none でない", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });
    const iosDisplay = await page
      .locator("#ios-section")
      .evaluate((el) => getComputedStyle(el).display);
    const androidDisplay = await page
      .locator("#android-section")
      .evaluate((el) => getComputedStyle(el).display);
    const eitherVisible = iosDisplay !== "none" || androidDisplay !== "none";
    expect(eitherVisible, `ios=${iosDisplay} android=${androidDisplay}`).toBeTruthy();
  });

  test("全チェック + 年齢選択でボタンが有効化される", async ({ page }) => {
    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) await checkboxes.nth(i).check();
    await page.locator("#age-group").selectOption("20s");
    await expect(page.locator("#download-btn")).not.toHaveClass(/btn-disabled/, { timeout: 5_000 });
  });

  test("「同意してダウンロード」をクリックして完了UIが表示される", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto(PORTAL_URL, { waitUntil: "networkidle" });
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) await checkboxes.nth(i).check();
    await page.locator("#age-group").selectOption("20s");

    // 完了UIに切り替わることを確認（mobileconfig URLリンクが現れる）
    const [consentRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/mdm/device/consent"), { timeout: 15_000 }),
      page.locator("#download-btn").click(),
    ]);

    expect(consentRes.status(), "consent API ステータス").toBe(200);
    const body = await consentRes.json();

    // 完了UIにダウンロードリンクが表示される
    await expect(page.getByRole("link", { name: /プロファイルをダウンロード/ })).toBeVisible({ timeout: 10_000 });
    expect(errors, `JSエラー: ${errors.join(", ")}`).toHaveLength(0);

    return body; // 後続テストで token を使えるよう返す
  });
});

// ─── mobileconfig API + plist 検証 ───────────────────────────
test.describe("mobileconfig ダウンロードと中身の検証", () => {
  // 同意APIでトークンを取得してから各テストを実行
  let token;

  test.beforeAll(async ({ request }) => {
    // PORTAL_URL に含まれる dealer_id を使用（dealer=c229b92e-... の部分）
    const dealerId = new URL(PORTAL_URL).searchParams.get("dealer");
    const res = await request.post(`${BASE}/mdm/device/consent`, {
      headers: { "Content-Type": "application/json" },
      data: {
        dealer_id: dealerId,
        user_agent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        consent_items: [
          "lockscreen_ads", "push_notifications", "webclip_install",
          "vpn_setup", "app_install", "data_collection",
        ],
        age_group: "20s",
      },
    });
    expect(res.ok(), `consent API: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    token = data.enrollment_token;
    expect(token).toBeTruthy();
  });

  test("HTTP 200 + 正しい Content-Type が返る", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("application/x-apple-aspen-config");
  });

  test("Content-Disposition が inline（attachment だと iOS でインストーラーが起動しない）", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const cd = res.headers()["content-disposition"] || "";
    expect(cd).toContain("inline");
    expect(cd).not.toContain("attachment");
  });

  test("Cache-Control が no-store（古いプロファイルをキャッシュしない）", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const cc = res.headers()["cache-control"] || "";
    expect(cc).toContain("no-store");
  });

  test("Content-Length が 0 より大きい（空でない）", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const body = await res.body();
    expect(body.length).toBeGreaterThan(200);
  });

  test("plist として正しくパースできる", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // plist モジュールがない場合は正規表現で検証
    expect(xml).toContain("<?xml version");
    expect(xml).toContain("<!DOCTYPE plist");
    expect(xml).toContain("<plist version=\"1.0\">");
    expect(xml).toContain("</plist>");
  });

  test("PayloadContent が空配列でない", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // <array/> または空の <array></array> がないことを確認
    expect(xml).not.toMatch(/<key>PayloadContent<\/key>\s*<array\/>/);
    expect(xml).not.toMatch(/<key>PayloadContent<\/key>\s*<array>\s*<\/array>/);

    // 少なくとも1つのペイロードが存在する（<dict> が PayloadContent 内にある）
    const payloadContentMatch = xml.match(/<key>PayloadContent<\/key>\s*<array>([\s\S]*?)<\/array>/);
    expect(payloadContentMatch, "PayloadContent array が見つからない").not.toBeNull();
    expect(payloadContentMatch[1]).toContain("<dict>");
  });

  test("必須フィールドが揃っている", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // トップレベル必須フィールド
    expect(xml).toContain("PayloadDisplayName");
    expect(xml).toContain("PayloadIdentifier");
    expect(xml).toContain("PayloadType");
    expect(xml).toContain("PayloadUUID");
    expect(xml).toContain("PayloadVersion");
    expect(xml).toContain("<string>Configuration</string>"); // PayloadType = Configuration
  });

  test("各ペイロードに PayloadType と PayloadUUID がある", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // PayloadContent 内を取得
    const arrayMatch = xml.match(/<key>PayloadContent<\/key>\s*<array>([\s\S]*?)<\/array>/);
    expect(arrayMatch).not.toBeNull();
    const arrayContent = arrayMatch[1];

    // ペイロードの dict ブロック数を数える
    const dictBlocks = arrayContent.match(/<dict>/g) || [];
    expect(dictBlocks.length).toBeGreaterThan(0);

    // PayloadType が存在する
    expect(arrayContent).toContain("PayloadType");
    expect(arrayContent).toContain("PayloadUUID");
  });

  test("WebClip URL が https で始まる", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // URL フィールドを抽出
    const urlMatch = xml.match(/<key>URL<\/key>\s*<string>(.*?)<\/string>/);
    if (urlMatch) {
      expect(urlMatch[1]).toMatch(/^https:\/\//);
      // ssp-platform.vercel.app であること（localhost でない）
      expect(urlMatch[1]).not.toContain("localhost");
      expect(urlMatch[1]).not.toContain("127.0.0.1");
    }
  });

  test("PayloadIdentifier が com.platform. で始まる（形式確認）", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`);
    const xml = await res.text();

    // トップレベルの PayloadIdentifier
    const idMatch = xml.match(/<key>PayloadIdentifier<\/key>\s*<string>(com\.platform\.mdm\.[0-9a-f-]+)<\/string>/);
    expect(idMatch, "PayloadIdentifier が期待形式でない:\n" + xml.substring(0, 500)).not.toBeNull();
  });

  test("無効トークンで 404 が返る", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=invalid-token-xyz`);
    expect(res.status()).toBe(404);
  });

  test("mobileconfig URL に最終リダイレクトがない（直接200）", async ({ request }) => {
    const res = await request.get(`${BASE}/mdm/ios/mobileconfig?token=${token}`, {
      maxRedirects: 0,
    });
    // リダイレクトなしで 200 が返る
    expect(res.status()).toBe(200);
  });
});
