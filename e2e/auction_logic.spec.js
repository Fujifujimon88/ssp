/**
 * auction_logic.spec.js
 * Batch 4: オークションロジック・エッジケース・パフォーマンス
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet } = require("./helpers/auth");

async function getActiveTagId(request) {
  const res = await apiGet(request, "/api/slots");
  const slots = await res.json();
  const active = slots.find((s) => s.active !== false);
  if (!active) throw new Error("アクティブなスロットがありません");
  return active.tag_id;
}

async function bid(request, { floorPrice = 0.5, tagId = null } = {}) {
  const { publisherId } = loadAuth();
  const slotId = tagId ?? (await getActiveTagId(request));
  return request.post("/v1/bid", {
    data: { publisherId, slotId, floorPrice, sizes: [[300, 250]] },
  });
}

// ── オークションロジック ─────────────────────────────────────

test.describe("オークションロジック", () => {
  test("フロアプライス超過（999）は bids が空になる", async ({ request }) => {
    // スロットがDBに存在するとslot.floor_priceで上書きされるため、
    // DBに存在しないfakeタグIDを使いリクエストのfloorPriceを有効にする
    const { publisherId } = loadAuth();
    const res = await request.post("/v1/bid", {
      data: {
        publisherId,
        slotId: "fake-tag-id-for-floor-test",
        floorPrice: 999,
        sizes: [[300, 250]],
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.bids).toHaveLength(0);
  });

  test("フロアプライス 0.01 では高確率で落札される", async ({ request }) => {
    // 5回試して1回でも落札されれば OK（確率的テスト）
    let won = false;
    for (let i = 0; i < 5; i++) {
      const res = await bid(request, { floorPrice: 0.01 });
      const body = await res.json();
      if (body.bids.length > 0) {
        won = true;
        break;
      }
    }
    expect(won).toBe(true);
  });

  test("落札CPMはフロアプライス以上である", async ({ request }) => {
    const floor = 0.5;
    let clearing = null;
    for (let i = 0; i < 5; i++) {
      const res = await bid(request, { floorPrice: floor });
      const body = await res.json();
      if (body.bids.length > 0) {
        clearing = body.bids[0].cpm;
        break;
      }
    }
    if (clearing !== null) {
      expect(clearing).toBeGreaterThanOrEqual(floor);
    }
  });

  test("winToken は /v1/win 使用後に再利用できない", async ({ request }) => {
    // winToken を取得
    let winToken = null;
    for (let i = 0; i < 5; i++) {
      const res = await bid(request, { floorPrice: 0.01 });
      const body = await res.json();
      if (body.bids.length > 0) {
        winToken = body.bids[0].winToken;
        break;
      }
    }
    expect(winToken).not.toBeNull();

    // 1回目: 成功
    const win1 = await request.get(`/v1/win?token=${winToken}`);
    expect(win1.ok()).toBeTruthy();

    // 2回目: トークン削除済みで404
    const win2 = await request.get(`/v1/win?token=${winToken}`);
    expect(win2.status()).toBe(404);
  });
});

// ── タイムアウト・パフォーマンス ─────────────────────────────

test.describe("タイムアウト・パフォーマンス", () => {
  test("mock-slow が登録されていても /v1/bid が 500ms 以内に返る", async ({
    request,
  }) => {
    // /health で mock-slow が登録されていることを確認
    const healthRes = await request.get("/health");
    const health = await healthRes.json();
    expect(health.dsps).toContain("mock-slow");

    // 時間計測
    const start = Date.now();
    const res = await bid(request, { floorPrice: 0.01 });
    const elapsed = Date.now() - start;

    expect(res.ok()).toBeTruthy();
    // mock-slow のタイムアウト(80ms) + 余裕 = 500ms以内
    expect(elapsed).toBeLessThan(500);
  });

  test("/api/reports/range?days=1 は 1件返す", async ({ request }) => {
    const res = await apiGet(request, "/api/reports/range?days=1");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveLength(1);
  });

  test("未来日付の日次レポートは impressions=0", async ({ request }) => {
    const future = new Date();
    future.setDate(future.getDate() + 30);
    const dateStr = future.toISOString().split("T")[0];

    const res = await apiGet(request, `/api/reports/daily?date_str=${dateStr}`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.impressions).toBe(0);
    expect(data.revenue_usd).toBe(0);
  });
});
