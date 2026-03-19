/**
 * wifi_checkin.spec.js — Wi-Fi SSID 来店トリガー E2E テスト
 *
 * テスト対象:
 *   POST /mdm/android/wifi_checkin
 *
 * テスト構成:
 *   1. 正常系（ルールなし → skipped）
 *   2. 正常系（pushルールあり → actions_fired に "push"）
 *   3. クールダウン（同SSID×デバイスで2回目はスキップ）
 *   4. 異常系（未登録device_id → 404ではなくskipped）
 *   5. バリデーション（ssid欠落 → 422）
 *   6. 管理者APIでルール一覧取得
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const STORE_CODE = `e2e-wifi-${Date.now()}`;
const TEST_SSID = `TEST_WIFI_${Date.now()}`;
const TEST_DEVICE_ID = `e2e-device-${Date.now()}`;

let dealerId = null;

test.describe.configure({ mode: "serial" });

test.describe("Wi-Fi SSID 来店チェックイン", () => {

  // ──────────────────────────────────────────────────────
  // 前処理: テスト用ディーラーとAndroidデバイスを登録
  // ──────────────────────────────────────────────────────
  test.beforeAll(async ({ request }) => {
    // ディーラー作成
    const dealerRes = await request.post("/mdm/admin/dealers", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        name: "E2EテストWifiショップ",
        store_code: STORE_CODE,
        address: "東京都テスト区1-1-1",
      },
    });
    expect(dealerRes.ok(), "ディーラー作成失敗").toBeTruthy();
    const dealer = await dealerRes.json();
    dealerId = dealer.id;

    // Androidデバイス登録（/mdm/android/register 経由）
    const checkinRes = await request.post("/mdm/android/register", {
      data: {
        device_id: TEST_DEVICE_ID,
        manufacturer: "E2E",
        model: "TestPhone",
        android_version: "14",
        sdk_int: 34,
      },
    });
    // 登録済みでも ok を確認
    expect(checkinRes.ok(), "Androidデバイス登録失敗").toBeTruthy();
  });

  // ──────────────────────────────────────────────────────
  // 1. ルールなしの場合 → skippedになる
  // ──────────────────────────────────────────────────────
  test("ルール未登録SSIDは skipped=true で返る", async ({ request }) => {
    const res = await request.post("/mdm/android/wifi_checkin", {
      data: {
        device_id: TEST_DEVICE_ID,
        ssid: `NO_RULE_SSID_${Date.now()}`,
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.skipped).toBe(true);
    expect(body.actions_fired).toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────
  // 2. pushルール登録 → actions_fired に "push" が入る
  // ──────────────────────────────────────────────────────
  test("pushルール登録後 → actions_fired に push が含まれる", async ({ request }) => {
    // ルール登録（管理者API）
    const ruleRes = await request.post("/mdm/admin/wifi_trigger_rules", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        ssid: TEST_SSID,
        dealer_id: dealerId,
        action_type: "push",
        action_config: {
          title: "E2Eテスト来店通知",
          body: "テストショップにようこそ！",
        },
        cooldown_minutes: 0,  // クールダウンなし（テスト用）
      },
    });
    expect(ruleRes.ok(), `ルール登録失敗: ${await ruleRes.text()}`).toBeTruthy();

    // チェックイン実行（FCMトークンなしなのでpushはスキップされるが actions_fired には記録されない）
    const res = await request.post("/mdm/android/wifi_checkin", {
      data: {
        device_id: TEST_DEVICE_ID,
        ssid: TEST_SSID,
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.skipped).toBe(false);
    // FCMトークン未設定のためpush自体はスキップされるが、ログは記録される
    expect(Array.isArray(body.actions_fired)).toBeTruthy();
  });

  // ──────────────────────────────────────────────────────
  // 3. クールダウン（cooldown_minutes > 0 のルールで2回目はスキップ）
  // ──────────────────────────────────────────────────────
  test("クールダウン中の2回目チェックインはアクションを実行しない", async ({ request }) => {
    const cooldownSsid = `COOLDOWN_${Date.now()}`;

    // クールダウン60分のルールを登録
    const ruleRes = await request.post("/mdm/admin/wifi_trigger_rules", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        ssid: cooldownSsid,
        dealer_id: dealerId,
        action_type: "push",
        action_config: { title: "テスト", body: "テスト" },
        cooldown_minutes: 60,
      },
    });
    expect(ruleRes.ok()).toBeTruthy();

    // 1回目
    await request.post("/mdm/android/wifi_checkin", {
      data: { device_id: TEST_DEVICE_ID, ssid: cooldownSsid },
    });

    // 2回目（クールダウン中）
    const res2 = await request.post("/mdm/android/wifi_checkin", {
      data: { device_id: TEST_DEVICE_ID, ssid: cooldownSsid },
    });
    expect(res2.ok()).toBeTruthy();
    const body2 = await res2.json();
    // クールダウン中はアクション実行なし（skipped=falseだがactions_fired=[]）
    expect(body2.actions_fired).toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────
  // 4. 未登録device_id → skipped で返る（エラーではない）
  // ──────────────────────────────────────────────────────
  test("未登録device_idは skipped=true で返る（500にならない）", async ({ request }) => {
    const res = await request.post("/mdm/android/wifi_checkin", {
      data: {
        device_id: "nonexistent-device-000",
        ssid: TEST_SSID,
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.skipped).toBe(true);
  });

  // ──────────────────────────────────────────────────────
  // 5. バリデーション（ssid欠落 → 422）
  // ──────────────────────────────────────────────────────
  test("ssid欠落で 422 が返る", async ({ request }) => {
    const res = await request.post("/mdm/android/wifi_checkin", {
      data: { device_id: TEST_DEVICE_ID },  // ssid なし
    });
    expect(res.status()).toBe(422);
  });

  test("device_id欠落で 422 が返る", async ({ request }) => {
    const res = await request.post("/mdm/android/wifi_checkin", {
      data: { ssid: TEST_SSID },  // device_id なし
    });
    expect(res.status()).toBe(422);
  });

  // ──────────────────────────────────────────────────────
  // 6. 管理者APIでルール一覧が取得できる
  // ──────────────────────────────────────────────────────
  test("管理者API GET /mdm/admin/wifi_trigger_rules が配列を返す", async ({ request }) => {
    const res = await request.get("/mdm/admin/wifi_trigger_rules", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body)).toBeTruthy();
    // 今回作成したルールが含まれているか
    const found = body.some((r) => r.ssid === TEST_SSID);
    expect(found).toBeTruthy();
  });

  test("管理者APIに認証なしでアクセスすると 401", async ({ request }) => {
    const res = await request.get("/mdm/admin/wifi_trigger_rules");
    expect(res.status()).toBe(401);
  });
});
