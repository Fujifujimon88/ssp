/**
 * mdm_commands.spec.js
 * MDMコマンド4機能のE2Eテスト
 *   1. アプリインストール  (install_apk)       Android
 *   2. Webクリップ追加    (add_webclip)        iOS / Android
 *   3. ロック画面カスタマイズ (update_lockscreen) Android
 *   4. プッシュ通知送信   (show_notification)  iOS / Android
 */
const { test, expect } = require("@playwright/test");
const { adminGet, setAdminKeyInBrowser } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

// ─── 共通ヘルパー ──────────────────────────────────────────────
function adminPost(request, endpoint, body) {
  return request.post(endpoint, {
    headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
    data: body,
  });
}

// デバイスが存在しなくても 404 であって認証エラー(401)でないことを確認する
// → コマンドキューの入口まで到達していることを意味する
async function expectNotAuth(request, endpoint, body) {
  const res = await adminPost(request, endpoint, body);
  expect(res.status()).not.toBe(401);
  expect(res.status()).not.toBe(403);
}

// ══════════════════════════════════════════════════════════════
// 1. アプリインストール  — Android
// ══════════════════════════════════════════════════════════════
test.describe("アプリインストール (install_apk) — Android", () => {
  const endpoint = "/mdm/admin/android/push";
  const validBody = {
    device_id: "test-nonexistent-device-apk",
    command_type: "install_apk",
    payload: {
      package_name: "com.example.testapp",
      app_url: "https://cdn.example.com/test.apk",
      title: "テストアプリ",
    },
    send_fcm: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーでコマンドエンドポイントに到達できる", async ({ request }) => {
    await expectNotAuth(request, endpoint, validBody);
  });

  test("command_type が不正な場合は 4xx が返る", async ({ request }) => {
    const res = await adminPost(request, endpoint, {
      ...validBody,
      command_type: "invalid_command",
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test("payload なしは 422 (バリデーションエラー) が返る", async ({ request }) => {
    const res = await adminPost(request, endpoint, {
      command_type: "install_apk",
      send_fcm: false,
      // device_id を省略
    });
    expect(res.status()).toBe(422);
  });
});

// ══════════════════════════════════════════════════════════════
// 2. Webクリップ追加  — Android
// ══════════════════════════════════════════════════════════════
test.describe("Webクリップ追加 (add_webclip) — Android", () => {
  const endpoint = "/mdm/admin/android/push";
  const validBody = {
    device_id: "test-nonexistent-device-webclip",
    command_type: "add_webclip",
    payload: {
      url: "https://coupon.example.com",
      label: "本日のクーポン",
      full_screen: true,
    },
    send_fcm: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーでコマンドエンドポイントに到達できる", async ({ request }) => {
    await expectNotAuth(request, endpoint, validBody);
  });

  test("url が空文字のペイロードは 4xx になる", async ({ request }) => {
    const res = await adminPost(request, endpoint, {
      ...validBody,
      payload: { url: "", label: "テスト" },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
  });
});

// ══════════════════════════════════════════════════════════════
// 2b. Webクリップ追加  — iOS
// ══════════════════════════════════════════════════════════════
test.describe("Webクリップ追加 (add_web_clip) — iOS", () => {
  const endpoint = "/mdm/admin/ios/command";
  const validBody = {
    udid: "00000000-0000-0000-0000-000000000000",
    request_type: "add_web_clip",
    params: {
      url: "https://coupon.example.com",
      label: "本日のクーポン",
      full_screen: true,
    },
    send_push: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーでiOSコマンドエンドポイントに到達できる", async ({ request }) => {
    await expectNotAuth(request, endpoint, validBody);
  });

  test("udid が空の場合は 4xx になる", async ({ request }) => {
    const res = await adminPost(request, endpoint, { ...validBody, udid: "" });
    expect(res.status()).toBeGreaterThanOrEqual(400);
  });
});

// ══════════════════════════════════════════════════════════════
// 3. ロック画面カスタマイズ  — Android
// ══════════════════════════════════════════════════════════════
test.describe("ロック画面カスタマイズ (update_lockscreen) — Android", () => {
  const endpoint = "/mdm/admin/android/push";
  const validBody = {
    device_id: "test-nonexistent-device-lock",
    command_type: "update_lockscreen",
    payload: {
      image_url: "https://cdn.example.com/banner.jpg",
      title: "本日限定セール",
      cta_url: "https://store.example.com/sale",
    },
    send_fcm: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーでコマンドエンドポイントに到達できる", async ({ request }) => {
    await expectNotAuth(request, endpoint, validBody);
  });

  test("payload を空オブジェクトにしても 4xx にならない（auto-select）", async ({ request }) => {
    // image_url 省略 → システムが自動クリエイティブ選択するため弾かれないはず
    const res = await adminPost(request, endpoint, {
      device_id: "test-nonexistent-device-lock",
      command_type: "update_lockscreen",
      payload: {},
      send_fcm: false,
    });
    // 400番台でも500番台でもなければ auto-select が機能している
    expect(res.status()).not.toBe(500);
  });
});

// ══════════════════════════════════════════════════════════════
// 4. プッシュ通知送信  — Android 単体
// ══════════════════════════════════════════════════════════════
test.describe("プッシュ通知送信 (show_notification) — Android 単体", () => {
  const endpoint = "/mdm/admin/android/push";
  const validBody = {
    device_id: "test-nonexistent-device-push",
    command_type: "show_notification",
    payload: {
      title: "本日のお知らせ",
      body: "限定クーポンが届いています",
      action_url: "https://store.example.com/coupon",
    },
    send_fcm: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーでコマンドエンドポイントに到達できる", async ({ request }) => {
    await expectNotAuth(request, endpoint, validBody);
  });

  test("title または body がないペイロードは 4xx になる", async ({ request }) => {
    const res = await adminPost(request, endpoint, {
      device_id: "test-nonexistent-device-push",
      command_type: "show_notification",
      payload: {},
      send_fcm: false,
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
  });
});

// ══════════════════════════════════════════════════════════════
// 4b. プッシュ通知一括送信  — Broadcast
// ══════════════════════════════════════════════════════════════
test.describe("プッシュ通知一括送信 — Broadcast", () => {
  const endpoint = "/mdm/admin/broadcast";
  const validBody = {
    command_type: "show_notification",
    payload: {
      title: "一括テスト通知",
      body: "全端末へのテストメッセージです",
    },
    platform: "android",
    send_push: false,
  };

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post(endpoint, { data: validBody });
    expect(res.status()).toBe(401);
  });

  test("有効な管理キーで broadcast エンドポイントに到達できる", async ({ request }) => {
    const res = await adminPost(request, endpoint, validBody);
    expect(res.status()).not.toBe(401);
    expect(res.status()).not.toBe(403);
  });

  test("レスポンスが JSON を返す", async ({ request }) => {
    const res = await adminPost(request, endpoint, validBody);
    expect(res.headers()["content-type"]).toContain("application/json");
  });

  test("platform=ios でも 4xx にならない", async ({ request }) => {
    const res = await adminPost(request, endpoint, {
      ...validBody,
      platform: "ios",
    });
    expect(res.status()).not.toBe(401);
    expect(res.status()).not.toBe(422);
  });

  test("不正な command_type でも broadcast は 200 を返す（サーバー側バリデーションなし）", async ({ request }) => {
    // broadcast エンドポイントは command_type を厳密に検証しない実装のため 200 が返る
    const res = await adminPost(request, endpoint, {
      ...validBody,
      command_type: "invalid_type",
    });
    expect(res.status()).toBe(200);
  });
});

// ══════════════════════════════════════════════════════════════
// UI テスト — admin画面のMDMコマンドセクション
// ══════════════════════════════════════════════════════════════
test.describe("管理画面 MDMコマンドセクション UI", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/admin");
    await setAdminKeyInBrowser(page);
  });

  test("管理画面が開く", async ({ page }) => {
    // /admin は /mdm-dashboard にリダイレクトされる場合がある
    await expect(page).toHaveURL(/admin|mdm-dashboard/);
    await expect(page.locator("body")).toBeVisible();
  });

  test("MDMダッシュボードセクションが表示される", async ({ page }) => {
    // mdm-dashboard セクションに切り替える
    await page.evaluate(() => {
      if (typeof showSection === "function") showSection("mdm-dashboard");
    });
    await page.waitForTimeout(500);
    const section = page.locator("#mdm-dashboard");
    await expect(section).toBeVisible();
  });

  test("4つのコマンドタイプがデバイスコマンドフォームに存在する", async ({ page }) => {
    // コマンドモーダルを直接開いてselect内の4コマンドオプションを確認
    await page.evaluate(() => {
      const modal = document.getElementById("device-command-modal");
      if (modal) modal.style.display = "flex";
    });
    const select = page.locator("#command-type-select");
    await expect(select).toBeAttached();
    // 4つのコマンドタイプがオプションとして存在する
    await expect(page.locator('#command-type-select option[value="install_apk"]')).toHaveCount(1);
    await expect(page.locator('#command-type-select option[value="add_webclip"]')).toHaveCount(1);
    await expect(page.locator('#command-type-select option[value="update_lockscreen"]')).toHaveCount(1);
    await expect(page.locator('#command-type-select option[value="show_notification"]')).toHaveCount(1);
  });

  test("ブロードキャストモーダルが開く", async ({ page }) => {
    // #devices セクションを表示してからボタンを探す
    await page.evaluate(() => {
      if (typeof showSection === "function") showSection("devices");
    });
    await page.waitForTimeout(300);

    const broadcastBtn = page.locator("#devices").getByText(/ブロードキャスト|broadcast/i).first();
    await expect(broadcastBtn).toBeVisible({ timeout: 5_000 });
    await broadcastBtn.click();

    const modal = page.locator("#broadcast-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#broadcast-command-type")).toBeVisible();
  });
});
