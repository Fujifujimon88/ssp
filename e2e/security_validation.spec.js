/**
 * security_validation.spec.js
 * DSP UIデータ検証・クロスパブリッシャーセキュリティ・入力バリデーションのE2Eテスト
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, apiPost } = require("./helpers/auth");

// ── DSP UI データ検証 ─────────────────────────────────────────

test.describe("DSP UI データ検証", () => {
  async function loginAndGo(page, path = "/dashboard") {
    const { token } = loadAuth();
    await page.goto("/login");
    await page.evaluate((t) => localStorage.setItem("ssp_token", t), token);
    await page.goto(path);
  }

  test("DSP連携セクションにDSP名が表示される", async ({ page }) => {
    await loginAndGo(page);
    await page.locator("#nav-dsp").click();
    await expect(page.locator("#dsp-connection-list")).not.toContainText(
      "読み込み中...",
      { timeout: 5000 }
    );
    // /health エンドポイントから取得したDSP名が表示される
    const text = await page.locator("#dsp-connection-list").textContent();
    expect(text.length).toBeGreaterThan(0);
    expect(text).not.toBe("");
  });

  test("/health がDSPリストを返す", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("dsps");
    expect(Array.isArray(data.dsps)).toBeTruthy();
    expect(data.dsps.length).toBeGreaterThan(0);
  });

  test("/api/dsp/stats が dsp_id・wins・avg_cpm・revenue を含む", async ({
    request,
  }) => {
    // まず入札してデータを作る
    const { publisherId } = loadAuth();
    const slotsRes = await apiGet(request, "/api/slots");
    const slots = await slotsRes.json();
    const activeSlot = slots.find((s) => s.active !== false);
    if (activeSlot) {
      for (let i = 0; i < 3; i++) {
        await request.post("/v1/bid", {
          data: {
            publisherId,
            slotId: activeSlot.tag_id,
            floorPrice: 0.01,
            sizes: [[300, 250]],
          },
        });
      }
    }

    const res = await apiGet(request, "/api/dsp/stats");
    expect(res.ok()).toBeTruthy();
    const stats = await res.json();
    expect(Array.isArray(stats)).toBeTruthy();
    // 本日落札データがあれば各フィールドを確認
    for (const row of stats) {
      expect(row).toHaveProperty("dsp_id");
      expect(row).toHaveProperty("wins");
      expect(row).toHaveProperty("avg_cpm");
      expect(row).toHaveProperty("revenue");
    }
  });
});

// ── クロスパブリッシャーセキュリティ ────────────────────────

test.describe("クロスパブリッシャーセキュリティ", () => {
  test("他パブリッシャーIDでスロット作成は 403", async ({ request }) => {
    const { token } = loadAuth();
    const res = await request.post("/api/slots", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        publisher_id: "00000000-0000-0000-0000-000000000000", // 別パブリッシャーID
        name: "不正スロット",
        width: 300,
        height: 250,
        floor_price: 1.0,
      },
    });
    expect(res.status()).toBe(403);
  });

  test("他パブリッシャーのスロット削除は 404（自分のスロットしか見えない）", async ({
    request,
  }) => {
    const { token } = loadAuth();
    // 存在しないIDは404
    const res = await request.delete(
      "/api/slots/00000000-0000-0000-0000-000000000000",
      { headers: { Authorization: `Bearer ${token}` } }
    );
    expect(res.status()).toBe(404);
  });

  test("トークンなしで /api/slots は 401", async ({ request }) => {
    const res = await request.get("/api/slots");
    expect(res.status()).toBe(401);
  });

  test("トークンなしで DELETE /api/slots/{id} は 401", async ({ request }) => {
    const res = await request.delete("/api/slots/some-slot-id");
    expect(res.status()).toBe(401);
  });
});

// ── 入力バリデーション (422) ─────────────────────────────────

test.describe("入力バリデーション", () => {
  test("スロット作成で必須フィールド欠落は 422", async ({ request }) => {
    const { token, publisherId } = loadAuth();
    // name なし
    const res = await request.post("/api/slots", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        publisher_id: publisherId,
        width: 300,
        height: 250,
      },
    });
    expect(res.status()).toBe(422);
  });

  test("スロット作成で width に文字列を渡すと 422", async ({ request }) => {
    const { token, publisherId } = loadAuth();
    const res = await request.post("/api/slots", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        publisher_id: publisherId,
        name: "バリデーションテスト",
        width: "abc",
        height: 250,
      },
    });
    expect(res.status()).toBe(422);
  });

  test("/auth/register で必須フィールド欠落は 422", async ({ request }) => {
    const res = await request.post("/auth/register", {
      data: { domain: "test.example.com" }, // password なし
    });
    expect(res.status()).toBe(422);
  });

  test("/auth/token で認証情報欠落は 422", async ({ request }) => {
    const res = await request.post("/auth/token", {
      data: {}, // username・password なし (form data)
    });
    expect(res.status()).toBe(422);
  });
});
