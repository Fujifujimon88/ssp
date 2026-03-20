/**
 * janet_integration.spec.js
 * JANet ASP 連携のE2Eテスト
 *
 * テスト対象エンドポイント:
 *   GET  /mdm/affiliate/click/{campaign_id}?device_id=xxx  クリック追跡 → JANetリダイレクト
 *   GET  /mdm/affiliate/postback/janet?uid=xxx&price=xxx   JANetポストバック受信
 *   POST /mdm/admin/affiliate/campaigns                    JANetフィールド付き案件登録
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const ADMIN_HEADERS = {
  "X-Admin-Key": ADMIN_KEY,
  "Content-Type": "application/json",
};

// ─── 共通ヘルパー ──────────────────────────────────────────────

function adminPost(request, endpoint, body) {
  return request.post(endpoint, { headers: ADMIN_HEADERS, data: body });
}

/**
 * テスト用 JANet 対応キャンペーンを作成し、campaign_id を返す
 */
async function createJanetCampaign(request, overrides = {}) {
  const res = await adminPost(request, "/mdm/admin/affiliate/campaigns", {
    name: `E2E-JANet-${Date.now()}`,
    category: "app",
    destination_url: "https://example.com/lp",
    reward_type: "cpi",
    reward_amount: 300,
    janet_media_id: "99901",
    janet_original_id: "88801",
    ...overrides,
  });
  const data = await res.json();
  return data.id;
}

// ══════════════════════════════════════════════════════════════
// 1. アフィリエイト案件 — JANetフィールド登録
// ══════════════════════════════════════════════════════════════

test.describe("アフィリエイト案件 JANetフィールド登録", () => {
  test("janet_media_id / janet_original_id を含む案件を登録できる", async ({ request }) => {
    const res = await adminPost(request, "/mdm/admin/affiliate/campaigns", {
      name: "JANet連携テスト案件",
      category: "app",
      destination_url: "https://example.com/app",
      reward_type: "cpi",
      reward_amount: 500,
      janet_media_id: "12345",
      janet_original_id: "67890",
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("id");
    expect(data.janet_media_id).toBe("12345");
    expect(data.janet_original_id).toBe("67890");
  });

  test("janet フィールドなしでも登録できる（通常案件）", async ({ request }) => {
    const res = await adminPost(request, "/mdm/admin/affiliate/campaigns", {
      name: "通常案件",
      category: "app",
      destination_url: "https://example.com/normal",
      reward_type: "cpi",
      reward_amount: 200,
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("id");
  });

  test("認証なしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/affiliate/campaigns", {
      headers: { "Content-Type": "application/json" },
      data: { name: "不正", destination_url: "https://x.com" },
    });
    expect(res.status()).toBe(401);
  });
});

// ══════════════════════════════════════════════════════════════
// 2. クリック追跡 — JANet リダイレクト
// ══════════════════════════════════════════════════════════════

test.describe("クリック追跡 /mdm/affiliate/click/{campaign_id}", () => {
  test("存在しないキャンペーンIDで 404 が返る", async ({ request }) => {
    const res = await request.get("/mdm/affiliate/click/nonexistent-campaign");
    expect(res.status()).toBe(404);
  });

  test("device_id なしでも destination_url にリダイレクトされる", async ({ page, request }) => {
    const campaignId = await createJanetCampaign(request);
    let finalUrl = null;
    page.on("request", (req) => {
      if (req.url().includes("example.com/lp")) finalUrl = req.url();
    });
    await page.route(/example\.com/, (route) => route.abort());
    await page.goto(`/mdm/affiliate/click/${campaignId}`).catch(() => {});
    expect(finalUrl).toContain("example.com/lp");
  });

  test("JANetキャンペーン + device_id で j-a-net.jp へリダイレクトされる", async ({ page, request }) => {
    const campaignId = await createJanetCampaign(request);
    const deviceId = "a1b2c3d4e5f6a7b8";

    let janetUrl = null;
    // regex でホスト名を確実にキャプチャ
    page.on("request", (req) => {
      if (req.url().includes("j-a-net.jp")) janetUrl = req.url();
    });
    await page.route(/j-a-net\.jp/, (route) => route.abort());

    await page.goto(
      `/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`
    ).catch(() => {});

    expect(janetUrl).not.toBeNull();
    expect(janetUrl).toContain("click.j-a-net.jp");
    // JANetのURL形式: https://click.j-a-net.jp/{media_id}/{original_id}/{device_id}
    expect(janetUrl).toContain("/99901/88801/");
    expect(janetUrl).toContain(deviceId);
  });

  test("JANet URLのパス構造が正しい（クエリパラメータではなくパスに device_id）", async ({ page, request }) => {
    const campaignId = await createJanetCampaign(request);
    const deviceId = "testdevice99";

    let janetUrl = null;
    page.on("request", (req) => {
      if (req.url().includes("j-a-net.jp")) janetUrl = req.url();
    });
    await page.route(/j-a-net\.jp/, (route) => route.abort());

    await page.goto(
      `/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`
    ).catch(() => {});

    expect(janetUrl).not.toBeNull();
    // パスに直接付与されている（?uid= 形式ではない）
    const url = new URL(janetUrl);
    expect(url.pathname).toMatch(new RegExp(`/${deviceId}$`));
    expect(url.searchParams.has("uid")).toBeFalsy();
    expect(url.searchParams.has("device_id")).toBeFalsy();
  });

  test("janet_media_id 未設定キャンペーンは destination_url にリダイレクト", async ({ page, request }) => {
    const res = await adminPost(request, "/mdm/admin/affiliate/campaigns", {
      name: `E2E-Normal-${Date.now()}`,
      category: "app",
      destination_url: "https://example.com/normal-lp",
      reward_type: "cpi",
      reward_amount: 100,
    });
    const data = await res.json();
    const campaignId = data.id;

    let normalUrl = null;
    page.on("request", (req) => {
      if (req.url().includes("example.com/normal-lp")) normalUrl = req.url();
    });
    await page.route(/example\.com/, (route) => route.abort());

    await page.goto(
      `/mdm/affiliate/click/${campaignId}?device_id=somedevice`
    ).catch(() => {});

    expect(normalUrl).toContain("example.com/normal-lp");
  });
});

// ══════════════════════════════════════════════════════════════
// 3. JANet ポストバック受信
// ══════════════════════════════════════════════════════════════

test.describe("JANetポストバック受信 /mdm/affiliate/postback/janet", () => {
  test("uid + price で 200 + ok が返る", async ({ request }) => {
    const res = await request.get(
      "/mdm/affiliate/postback/janet?uid=testdevice001&price=300&ad=88801"
    );
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });

  test("uid なしでも 200 が返る（JANetへのリトライ防止）", async ({ request }) => {
    const res = await request.get("/mdm/affiliate/postback/janet?price=300");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });

  test("price が数値でなくても 200 が返る（バリデーション寛容）", async ({ request }) => {
    const res = await request.get(
      "/mdm/affiliate/postback/janet?uid=testdevice002&price=invalid"
    );
    expect(res.ok()).toBeTruthy();
  });

  test("同一 uid の2回目送信（冪等性）も 200 が返る", async ({ request }) => {
    const uid = `idem-device-${Date.now()}`;
    const first = await request.get(
      `/mdm/affiliate/postback/janet?uid=${uid}&price=500`
    );
    expect(first.ok()).toBeTruthy();

    const second = await request.get(
      `/mdm/affiliate/postback/janet?uid=${uid}&price=500`
    );
    expect(second.ok()).toBeTruthy();
    const data = await second.json();
    expect(data.status).toBe("ok");
  });

  test("レスポンスが JSON を返す", async ({ request }) => {
    const res = await request.get(
      "/mdm/affiliate/postback/janet?uid=testdevice003&price=100"
    );
    expect(res.headers()["content-type"]).toContain("application/json");
  });
});

// ══════════════════════════════════════════════════════════════
// 4. クリック → ポストバック 連携フロー
// ══════════════════════════════════════════════════════════════

test.describe("クリック → ポストバック 連携フロー", () => {
  test("クリック記録後にポストバックを受信するとCV計上される", async ({ page, request }) => {
    const deviceId = `flow-test-${Date.now()}`;
    const campaignId = await createJanetCampaign(request);

    // ① クリック記録（JANetへのリダイレクトはインターセプト）
    await page.route("**j-a-net.jp**", (route) => route.abort());
    await page.goto(
      `/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`
    ).catch(() => {});

    // ② JANetからポストバック受信（CV発生通知）
    const pbRes = await request.get(
      `/mdm/affiliate/postback/janet?uid=${deviceId}&price=300&ad=88801`
    );
    expect(pbRes.ok()).toBeTruthy();
    const pbData = await pbRes.json();
    expect(pbData.status).toBe("ok");

    // ③ CV一覧に記録されているか確認
    const cvRes = await request.get("/mdm/admin/affiliate/conversions", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(cvRes.ok()).toBeTruthy();
    const conversions = await cvRes.json();
    const janetCvs = conversions.filter((c) => c.source === "janet");
    expect(janetCvs.length).toBeGreaterThan(0);
  });
});
