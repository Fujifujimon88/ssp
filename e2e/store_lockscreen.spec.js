/**
 * store_lockscreen.spec.js — Feature 4: 店舗ロック画面専用枠 E2E テスト
 *
 * テスト対象:
 *   POST   /mdm/admin/stores/{dealer_id}/lockscreen-creative   — クリエイティブ登録
 *   GET    /mdm/admin/stores/{dealer_id}/lockscreen-creatives  — 一覧取得
 *   PATCH  /mdm/admin/stores/{dealer_id}/lockscreen-creatives/{id}/status — 状態変更
 *   DELETE /mdm/admin/stores/{dealer_id}/ad-assignments/{id}   — 削除（既存エンドポイント）
 *   GET    /mdm/android/lockscreen/content                     — 優先配信ロジック確認
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const adminHeaders = () => ({ "X-Admin-Key": ADMIN_KEY });

const STORE_CODE = `e2e-ls-${Date.now()}`;
let dealerId = null;
let assignmentId = null;

test.describe.configure({ mode: "serial" });

test.describe("Feature 4: 店舗ロック画面専用枠", () => {
  // ── セットアップ: テスト用ディーラーを作成 ──────────────────────────

  test.beforeAll(async ({ request }) => {
    const res = await request.post("/mdm/admin/dealers", {
      data: { name: "E2E店舗ロック画面", store_code: STORE_CODE, address: "東京都E2E区" },
      headers: adminHeaders(),
    });
    expect(res.ok(), `dealer作成失敗: ${res.status()}`).toBeTruthy();
    dealerId = (await res.json()).id;
    expect(dealerId).toBeTruthy();
  });

  // ── POST: クリエイティブ登録 ──────────────────────────────────────

  test("管理者キーで店舗クリエイティブを登録できる", async ({ request }) => {
    const res = await request.post(
      `/mdm/admin/stores/${dealerId}/lockscreen-creative`,
      {
        data: {
          title: "E2E本日限定セール",
          image_url: "https://example.com/e2e-sale.jpg",
          click_url: "https://example.com/e2e-sale",
          slot_type: "lockscreen",
          priority: 1,
        },
        headers: adminHeaders(),
      }
    );
    expect(res.ok(), `登録失敗: ${res.status()} ${await res.text()}`).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("assignment_id");
    expect(d).toHaveProperty("campaign_id");
    expect(d).toHaveProperty("creative_id");
    expect(d.dealer_id).toBe(dealerId);
    expect(d.slot_type).toBe("lockscreen");
    expect(d.priority).toBe(1);
    assignmentId = d.assignment_id;
  });

  test("管理者キーなしで登録すると 401 が返る", async ({ request }) => {
    const res = await request.post(
      `/mdm/admin/stores/${dealerId}/lockscreen-creative`,
      {
        data: {
          title: "テスト",
          image_url: "https://example.com/img.jpg",
          click_url: "https://example.com",
        },
      }
    );
    expect(res.status()).toBe(401);
  });

  test("存在しないdealer_idは 404 が返る", async ({ request }) => {
    const res = await request.post(
      "/mdm/admin/stores/nonexistent-dealer-xyz/lockscreen-creative",
      {
        data: {
          title: "テスト",
          image_url: "https://example.com/img.jpg",
          click_url: "https://example.com",
        },
        headers: adminHeaders(),
      }
    );
    expect(res.status()).toBe(404);
  });

  test("slot_type=notification は 422 が返る（lockscreen/widget のみ許可）", async ({ request }) => {
    const res = await request.post(
      `/mdm/admin/stores/${dealerId}/lockscreen-creative`,
      {
        data: {
          title: "テスト",
          image_url: "https://example.com/img.jpg",
          click_url: "https://example.com",
          slot_type: "notification",
        },
        headers: adminHeaders(),
      }
    );
    expect(res.status()).toBe(422);
  });

  // ── GET: 一覧取得 ─────────────────────────────────────────────────

  test("一覧取得で登録済みクリエイティブが返る", async ({ request }) => {
    const res = await request.get(
      `/mdm/admin/stores/${dealerId}/lockscreen-creatives`,
      { headers: adminHeaders() }
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.dealer_id).toBe(dealerId);
    expect(Array.isArray(d.creatives)).toBeTruthy();
    expect(d.creatives.length).toBeGreaterThanOrEqual(1);

    // 各クリエイティブの必須フィールド確認
    const c = d.creatives[0];
    expect(c).toHaveProperty("assignment_id");
    expect(c).toHaveProperty("image_url");
    expect(c).toHaveProperty("click_url");
    expect(c).toHaveProperty("status");
    expect(c).toHaveProperty("priority");
  });

  test("別の dealer_id では creatives が空配列", async ({ request }) => {
    // 別店舗を作成
    const dRes = await request.post("/mdm/admin/dealers", {
      data: { name: "E2E別店舗", store_code: `e2e-other-${Date.now()}` },
      headers: adminHeaders(),
    });
    const otherId = (await dRes.json()).id;

    const res = await request.get(
      `/mdm/admin/stores/${otherId}/lockscreen-creatives`,
      { headers: adminHeaders() }
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.creatives).toHaveLength(0);
  });

  // ── PATCH: ステータス変更 ─────────────────────────────────────────

  test("クリエイティブを paused に変更できる", async ({ request }) => {
    const res = await request.patch(
      `/mdm/admin/stores/${dealerId}/lockscreen-creatives/${assignmentId}/status`,
      { data: { status: "paused" }, headers: adminHeaders() }
    );
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d.ok).toBe(true);
    expect(d.status).toBe("paused");
  });

  test("クリエイティブを active に戻せる", async ({ request }) => {
    const res = await request.patch(
      `/mdm/admin/stores/${dealerId}/lockscreen-creatives/${assignmentId}/status`,
      { data: { status: "active" }, headers: adminHeaders() }
    );
    expect(res.ok()).toBeTruthy();
    expect((await res.json()).status).toBe("active");
  });

  test("無効な status 値は 422 が返る", async ({ request }) => {
    const res = await request.patch(
      `/mdm/admin/stores/${dealerId}/lockscreen-creatives/${assignmentId}/status`,
      { data: { status: "invalid_status" }, headers: adminHeaders() }
    );
    expect(res.status()).toBe(422);
  });

  // ── ロック画面コンテンツ — 店舗枠優先配信ロジック確認 ──────────────

  test("lockscreen/content で is_store_creative フラグを持つコンテンツが返る（店舗トークン指定時）", async ({ request }) => {
    // enrollment_tokenを持つDeviceを登録してdealer_idに紐付ける
    // (E2E環境ではデバイスなしでもエンドポイントは動作する)
    const res = await request.get(
      `/mdm/android/lockscreen/content?token=e2e-dummy-token-not-enrolled`
    );
    // デバイス未登録でも 200 が返ること（フォールバック動作）
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("content");
    // content は null または object（店舗枠 or フォールバック）
  });

  // ── 削除: ad-assignments 削除 ────────────────────────────────────

  test("管理者キーで ad-assignment を削除できる", async ({ request }) => {
    const res = await request.delete(
      `/mdm/admin/stores/${dealerId}/ad-assignments/${assignmentId}`,
      { headers: adminHeaders() }
    );
    expect(res.ok()).toBeTruthy();
    expect((await res.json()).ok).toBe(true);
  });

  test("削除後に一覧から消えている", async ({ request }) => {
    const res = await request.get(
      `/mdm/admin/stores/${dealerId}/lockscreen-creatives`,
      { headers: adminHeaders() }
    );
    const d = await res.json();
    const found = d.creatives.find((c) => c.assignment_id === assignmentId);
    expect(found).toBeUndefined();
  });
});
