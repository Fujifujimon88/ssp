/**
 * bid.spec.js
 * SSPコア入札フロー（/v1/bid, /v1/win, /v1/ad）のE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, apiPost } = require("./helpers/auth");

async function getFirstSlotTagId(request) {
  const res = await apiGet(request, "/api/slots");
  const slots = await res.json();
  if (!Array.isArray(slots) || slots.length === 0) {
    throw new Error("スロットが存在しません。先にスロットを作成してください。");
  }
  return slots[0].tag_id;
}

test.describe("POST /v1/bid 入札リクエスト", () => {
  test("入札リクエストが bids 配列を返す", async ({ request }) => {
    const { publisherId } = loadAuth();
    const tagId = await getFirstSlotTagId(request);

    const res = await request.post("/v1/bid", {
      data: {
        publisherId,
        slotId: tagId,
        floorPrice: 0.5,
        sizes: [[300, 250]],
      },
    });

    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("bids");
    expect(Array.isArray(body.bids)).toBeTruthy();
  });

  test("落札時に winToken が含まれる", async ({ request }) => {
    const { publisherId } = loadAuth();
    const tagId = await getFirstSlotTagId(request);

    // フロアを極低にして落札確率を上げる
    let winToken = null;
    for (let i = 0; i < 5; i++) {
      const res = await request.post("/v1/bid", {
        data: { publisherId, slotId: tagId, floorPrice: 0.01, sizes: [[300, 250]] },
      });
      const body = await res.json();
      if (body.bids && body.bids.length > 0) {
        winToken = body.bids[0].winToken;
        break;
      }
    }

    expect(winToken).not.toBeNull();
    expect(typeof winToken).toBe("string");
    expect(winToken.length).toBeGreaterThan(0);
  });

  test("publisherId なしで 400 が返る", async ({ request }) => {
    const tagId = await getFirstSlotTagId(request);
    const res = await request.post("/v1/bid", {
      data: { slotId: tagId, floorPrice: 0.5 },
    });
    expect(res.status()).toBe(400);
  });
});

test.describe("GET /v1/win 落札通知", () => {
  test("有効な winToken で {status: ok} が返る", async ({ request }) => {
    const { publisherId } = loadAuth();
    const tagId = await getFirstSlotTagId(request);

    let winToken = null;
    for (let i = 0; i < 5; i++) {
      const res = await request.post("/v1/bid", {
        data: { publisherId, slotId: tagId, floorPrice: 0.01, sizes: [[300, 250]] },
      });
      const body = await res.json();
      if (body.bids && body.bids.length > 0) {
        winToken = body.bids[0].winToken;
        break;
      }
    }

    expect(winToken).not.toBeNull();

    const winRes = await request.get(`/v1/win?token=${winToken}`);
    expect(winRes.ok()).toBeTruthy();
    const data = await winRes.json();
    expect(data.status).toBe("ok");
  });

  test("無効なトークンで 404 が返る", async ({ request }) => {
    const res = await request.get("/v1/win?token=invalid-token-xyz");
    expect(res.status()).toBe(404);
  });
});

test.describe("GET /v1/ad/{token} 広告クリエイティブ配信", () => {
  test("有効なトークンで HTML が返る", async ({ request }) => {
    const { publisherId } = loadAuth();
    const tagId = await getFirstSlotTagId(request);

    let winToken = null;
    for (let i = 0; i < 5; i++) {
      const res = await request.post("/v1/bid", {
        data: { publisherId, slotId: tagId, floorPrice: 0.01, sizes: [[300, 250]] },
      });
      const body = await res.json();
      if (body.bids && body.bids.length > 0) {
        winToken = body.bids[0].winToken;
        break;
      }
    }

    expect(winToken).not.toBeNull();

    const adRes = await request.get(`/v1/ad/${winToken}`);
    expect(adRes.ok()).toBeTruthy();
    const contentType = adRes.headers()["content-type"] || "";
    expect(contentType).toContain("text/html");
  });

  test("無効なトークンで 404 が返る", async ({ request }) => {
    const res = await request.get("/v1/ad/invalid-token-xyz");
    expect(res.status()).toBe(404);
  });
});
