/**
 * mdm_affiliate.spec.js
 * GTM LP・アフィリエイト案件API・クリック追跡のE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { adminGet } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

// ─────────────────────────────────────────────────────────────
// GTM LP（/mdm/lp/{campaign_id}）
// ─────────────────────────────────────────────────────────────

test.describe("GTM LP /mdm/lp/{campaign_id}", () => {
  test("存在しないキャンペーンIDで 404 が返る", async ({ request }) => {
    const res = await request.get("/mdm/lp/nonexistent-campaign-id");
    expect(res.status()).toBe(404);
  });
});

// ─────────────────────────────────────────────────────────────
// アフィリエイト案件API（/mdm/admin/affiliate/campaigns）
// ─────────────────────────────────────────────────────────────

test.describe("アフィリエイト案件API /mdm/admin/affiliate/campaigns", () => {
  test("GET で 200 + 配列が返る", async ({ request }) => {
    const res = await adminGet(request, "/mdm/admin/affiliate/campaigns");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("GET 認証なしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/campaigns");
    expect(res.status()).toBe(401);
  });

  test("POST で新規案件を登録すると 200 + id が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/affiliate/campaigns", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: {
        name: "E2Eテスト案件",
        category: "app",
        destination_url: "https://example.com/lp",
        reward_type: "cpi",
        reward_amount: 500,
      },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("id");
    expect(data).toHaveProperty("name");
  });

  test("POST 認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/affiliate/campaigns", {
      headers: { "Content-Type": "application/json" },
      data: {
        name: "不正案件",
        category: "app",
        destination_url: "https://example.com/lp",
      },
    });
    expect(res.status()).toBe(401);
  });
});

// ─────────────────────────────────────────────────────────────
// アフィリエイトクリック追跡（/mdm/affiliate/click/{campaign_id}）
// ─────────────────────────────────────────────────────────────

test.describe("アフィリエイトクリック追跡 /mdm/affiliate/click/{campaign_id}", () => {
  test("存在しないキャンペーンIDで 404 が返る", async ({ request }) => {
    const res = await request.get("/mdm/affiliate/click/nonexistent");
    expect(res.status()).toBe(404);
  });
});
