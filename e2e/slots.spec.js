/**
 * slots.spec.js
 * 広告スロットCRUDのE2Eテスト（JWT API経由）
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, apiPost } = require("./helpers/auth");

test.describe("広告スロット CRUD", () => {
  let createdSlotId = null;

  test("スロット一覧を取得できる", async ({ request }) => {
    const res = await apiGet(request, "/api/slots");
    expect(res.ok()).toBeTruthy();
    const slots = await res.json();
    expect(Array.isArray(slots)).toBeTruthy();
  });

  test("トークンなしでスロット一覧は401が返る", async ({ request }) => {
    const res = await request.get("/api/slots");
    expect(res.status()).toBe(401);
  });

  test("新しい広告スロットを作成できる", async ({ request }) => {
    const { publisherId } = loadAuth();
    const res = await apiPost(request, "/api/slots", {
      publisher_id: publisherId,
      name: `E2Eテストスロット_${Date.now()}`,
      format: "banner",
      width: 300,
      height: 250,
      floor_price: 0.5,
      position: 1,
    });
    expect(res.ok()).toBeTruthy();
    const slot = await res.json();
    expect(slot).toHaveProperty("id");
    expect(slot).toHaveProperty("tag_id");
    expect(slot.active).toBe(true);
    createdSlotId = slot.id;
  });

  test("作成したスロットが一覧に表示される", async ({ request }) => {
    const res = await apiGet(request, "/api/slots");
    const slots = await res.json();
    expect(slots.length).toBeGreaterThan(0);
    expect(slots.some((s) => s.id === createdSlotId)).toBeTruthy();
  });

  test("スロットのPrebid.jsタグを取得できる", async ({ request }) => {
    // createdSlotId が前のテストで設定されていなければスキップ
    if (!createdSlotId) {
      test.skip();
      return;
    }
    const res = await apiGet(request, `/api/slots/${createdSlotId}/tag`);
    expect(res.ok()).toBeTruthy();
    const tag = await res.json();
    expect(tag).toHaveProperty("head_tag");
    expect(tag).toHaveProperty("body_tag");
    expect(tag.head_tag).toContain("pbjs");
  });

  test("他パブリッシャーのスロットは作成不可（403）", async ({ request }) => {
    const res = await apiPost(request, "/api/slots", {
      publisher_id: "other-publisher-id-that-does-not-exist",
      name: "不正スロット",
      format: "banner",
      width: 300,
      height: 250,
    });
    expect(res.status()).toBe(403);
  });

  test("全スロットタグ一括取得できる", async ({ request }) => {
    const res = await apiGet(request, "/api/tags/full");
    expect(res.ok()).toBeTruthy();
    const tags = await res.json();
    expect(tags).toHaveProperty("head_tag");
    expect(tags).toHaveProperty("body_tags");
  });
});
