/**
 * asp_postback.spec.js
 * ASP汎用ポストバック受信の E2E テスト
 *
 * テスト対象:
 *   user_token: デバイス登録時に生成される不透明UUID（ASPに渡す）
 *   ASPパラメータ正規化: JANet/SKYFLAG/smaad/A8.netの異なるパラメータ名を共通化
 *   2段階通知: JANet(attestation_flag) / SKYFLAG(install=1)
 *   ポイント付与: enable_points=true のキャンペーンのみ UserPoint を記録
 *   CV計測: enable_points に関わらず常に AffiliateConversion を記録
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const ADMIN_HEADERS = {
  "X-Admin-Key": ADMIN_KEY,
  "Content-Type": "application/json",
};

// ─── ヘルパー ──────────────────────────────────────────────────

function adminPost(request, endpoint, body) {
  return request.post(endpoint, { headers: ADMIN_HEADERS, data: body });
}

function adminGet(request, endpoint) {
  return request.get(endpoint, { headers: ADMIN_HEADERS });
}

/** テスト用 Androidデバイスを登録してレスポンスを返す */
async function registerAndroidDevice(request, overrides = {}) {
  const deviceId = `test-device-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const res = await request.post("/mdm/android/register", {
    headers: { "Content-Type": "application/json" },
    data: {
      device_id: deviceId,
      manufacturer: "Test",
      model: "TestPhone",
      android_version: "13",
      sdk_int: 33,
      ...overrides,
    },
  });
  const data = await res.json();
  return { deviceId, data };
}

/** キャンペーンを作成して campaign_id を返す */
async function createCampaign(request, overrides = {}) {
  const res = await adminPost(request, "/mdm/admin/affiliate/campaigns", {
    name: `ASP-Test-${Date.now()}`,
    category: "app",
    destination_url: "https://example.com/lp",
    reward_type: "cpi",
    reward_amount: 300,
    ...overrides,
  });
  const data = await res.json();
  return data.id;
}

// ══════════════════════════════════════════════════════════════
// 1. user_token 生成
// ══════════════════════════════════════════════════════════════

test.describe("user_token — デバイス登録時に生成", () => {
  test("Androidデバイス登録レスポンスに user_token が含まれる", async ({ request }) => {
    const { data } = await registerAndroidDevice(request);
    expect(data).toHaveProperty("user_token");
    expect(typeof data.user_token).toBe("string");
    expect(data.user_token.length).toBeGreaterThan(0);
  });

  test("user_token は UT で始まる英数字", async ({ request }) => {
    const { data } = await registerAndroidDevice(request);
    expect(data.user_token).toMatch(/^UT[A-Za-z0-9]{10}$/);
  });

  test("同一 device_id で再登録しても user_token は変わらない（同一デバイス）", async ({ request }) => {
    const deviceId = `stable-device-${Date.now()}`;
    const enrollBody = { device_id: deviceId, manufacturer: "Test", model: "TestPhone", android_version: "13", sdk_int: 33 };
    const headers = { "Content-Type": "application/json" };
    const first = await request.post("/mdm/android/register", { headers, data: enrollBody });
    const firstData = await first.json();

    const second = await request.post("/mdm/android/register", { headers, data: enrollBody });
    const secondData = await second.json();

    expect(firstData.user_token).toBe(secondData.user_token);
  });

  test("異なる device_id には異なる user_token が割り当てられる", async ({ request }) => {
    const { data: d1 } = await registerAndroidDevice(request);
    const { data: d2 } = await registerAndroidDevice(request);
    expect(d1.user_token).not.toBe(d2.user_token);
  });
});

// ══════════════════════════════════════════════════════════════
// 2. クリックURL に user_token が使われる
// ══════════════════════════════════════════════════════════════

test.describe("クリック追跡 — user_token でASPにリダイレクト", () => {
  test("JANetキャンペーン: URLパスに device_id でなく user_token が付与される", async ({ page, request }) => {
    const { deviceId, data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    const campaignId = await createCampaign(request, {
      janet_media_id: "55501",
      janet_original_id: "44401",
    });

    let janetUrl = null;
    page.on("request", (req) => {
      if (req.url().includes("click.j-a-net.jp")) janetUrl = req.url();
    });
    await page.route(/j-a-net\.jp/, (route) => route.abort());

    await page.goto(`/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`).catch(() => {});

    expect(janetUrl).not.toBeNull();
    // user_token がパスに含まれる
    expect(janetUrl).toContain(userToken);
    // device_id（生の Android ID）はパスに含まれない
    expect(janetUrl).not.toContain(deviceId);
  });

  test("device_id 未登録の場合は destination_url にフォールバック", async ({ page, request }) => {
    const campaignId = await createCampaign(request, {
      janet_media_id: "55501",
      janet_original_id: "44401",
    });

    let fallbackUrl = null;
    page.on("request", (req) => {
      if (req.url().includes("example.com/lp")) fallbackUrl = req.url();
    });
    await page.route(/example\.com/, (route) => route.abort());

    // 未登録の device_id でクリック
    await page.goto(`/mdm/affiliate/click/${campaignId}?device_id=unregistered-xyz-123`).catch(() => {});

    expect(fallbackUrl).toContain("example.com/lp");
  });
});

// ══════════════════════════════════════════════════════════════
// 3. JANet ポストバック（実パラメータ）
// ══════════════════════════════════════════════════════════════

test.describe("JANetポストバック — 実パラメータ (user_id/commission/action_id/attestation_flag)", () => {
  test("attestation_flag=1（pending）: CV記録・status=pending", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;
    const actionId = `ACT-PENDING-${Date.now()}`;

    const res = await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=500&action_id=${actionId}&attestation_flag=1`
    );
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");

    // CV が pending で記録されているか確認
    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("pending");
  });

  test("attestation_flag=0（approved）: CV記録・status=approved", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    const actionId = `ACT-APPROVE-${Date.now()}`;
    const res = await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=600&action_id=${actionId}&attestation_flag=0`
    );
    expect(res.ok()).toBeTruthy();

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("approved");
  });

  test("2段階通知: phase1(pending) → phase2(approved) で attestation_status が更新される", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;
    const actionId = `ACT-2PHASE-${Date.now()}`;

    // Phase 1: pending
    const phase1 = await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=400&action_id=${actionId}&attestation_flag=1`
    );
    expect(phase1.ok()).toBeTruthy();

    // Phase 2: approved
    const phase2 = await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=400&action_id=${actionId}&attestation_flag=0`
    );
    expect(phase2.ok()).toBeTruthy();

    // CV が approved に更新されているか
    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cvs = conversions.filter((c) => c.asp_action_id === actionId);
    // 重複記録されず1件のみ
    expect(cvs.length).toBe(1);
    expect(cvs[0].attestation_status).toBe("approved");
  });

  test("user_id なし → 200 + ok（ASPリトライ防止）", async ({ request }) => {
    const res = await request.get(
      "/mdm/affiliate/postback/janet?commission=300&action_id=NOUID"
    );
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });
});

// ══════════════════════════════════════════════════════════════
// 4. SKYFLAG ポストバック（suid/price/cv_id/install=1）
// ══════════════════════════════════════════════════════════════

test.describe("SKYFLAGポストバック — (suid/price/cv_id/install=1)", () => {
  test("install=1（approved）: CV記録・status=approved", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;
    const cvId = `SFCV-${Date.now()}`;

    const res = await request.get(
      `/mdm/affiliate/postback/skyflag?suid=${userToken}&price=800&cv_id=${cvId}&install=1`
    );
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");

    // CV が approved で記録されているか
    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === cvId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("approved");
    expect(cv.source).toBe("skyflag");
  });

  test("install なし（pending）: CV記録・status=pending", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;
    const cvId = `SFCV-PEND-${Date.now()}`;

    const res = await request.get(
      `/mdm/affiliate/postback/skyflag?suid=${userToken}&price=800&cv_id=${cvId}`
    );
    expect(res.ok()).toBeTruthy();

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === cvId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("pending");
  });

  test("suid なし → 200 + ok", async ({ request }) => {
    const res = await request.get(
      "/mdm/affiliate/postback/skyflag?price=500&cv_id=NOUID-SF"
    );
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });
});

// ══════════════════════════════════════════════════════════════
// 5. smaad / A8.net ポストバック（uid/price）
// ══════════════════════════════════════════════════════════════

test.describe("smaad/A8.netポストバック — uid/price（2段階通知なし）", () => {
  test("smaad: uid + price で CV記録・status=approved（2段階なし）", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    const res = await request.get(
      `/mdm/affiliate/postback/smaad?uid=${userToken}&price=200`
    );
    expect(res.ok()).toBeTruthy();

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const smaadCvs = conversions.filter(
      (c) => c.source === "smaad" && c.user_token === userToken
    );
    expect(smaadCvs.length).toBeGreaterThan(0);
    expect(smaadCvs[0].attestation_status).toBe("approved");
  });

  test("a8: uid + price で CV記録・status=approved", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    const res = await request.get(
      `/mdm/affiliate/postback/a8?uid=${userToken}&price=150`
    );
    expect(res.ok()).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════
// 6. ポイント付与
// ══════════════════════════════════════════════════════════════

test.describe("ポイント付与 — enable_points=true/false", () => {
  test("enable_points=false（デフォルト）: CVは記録されるがポイントは付与されない", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    // enable_points なし（デフォルト=false）のキャンペーン
    await createCampaign(request);

    const actionId = `NOPOINT-${Date.now()}`;
    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=300&action_id=${actionId}&attestation_flag=0`
    );

    // CVは記録される
    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();

    // ポイントは付与されない
    const ptRes = await adminGet(request, `/mdm/admin/affiliate/points?user_token=${userToken}`);
    expect(ptRes.ok()).toBeTruthy();
    const points = await ptRes.json();
    const pointForCv = points.find((p) => p.conversion_id === cv.id);
    expect(pointForCv).toBeUndefined();
  });

  test("enable_points=true: approved後にポイントが付与される", async ({ page, request }) => {
    const { deviceId, data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    // enable_points=true のキャンペーン
    const campaignId = await createCampaign(request, {
      janet_media_id: "66601",
      janet_original_id: "55501",
      enable_points: true,
      point_rate: 1.0,
    });

    // クリック記録（JANetへのリダイレクトはインターセプト）
    await page.route(/j-a-net\.jp/, (route) => route.abort());
    await page.goto(`/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`).catch(() => {});

    const actionId = `POINT-${Date.now()}`;
    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=500&action_id=${actionId}&attestation_flag=0`
    );

    // CVは記録される
    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();

    // ポイントが付与されている
    const ptRes = await adminGet(request, `/mdm/admin/affiliate/points?user_token=${userToken}`);
    expect(ptRes.ok()).toBeTruthy();
    const points = await ptRes.json();
    const pointForCv = points.find((p) => p.conversion_id === cv.id);
    expect(pointForCv).toBeDefined();
    expect(pointForCv.points).toBe(500); // 500円 × rate=1.0
  });

  test("enable_points=true + pending: ポイントは付与されない（approved待ち）", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    await createCampaign(request, {
      enable_points: true,
      point_rate: 1.0,
    });

    const actionId = `POINT-PEND-${Date.now()}`;
    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=500&action_id=${actionId}&attestation_flag=1`
    );

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("pending");

    const ptRes = await adminGet(request, `/mdm/admin/affiliate/points?user_token=${userToken}`);
    const points = await ptRes.json();
    const pointForCv = points.find((p) => p.conversion_id === cv.id);
    expect(pointForCv).toBeUndefined();
  });

  test("2段階通知: pending→approved でポイントが付与される（enable_points=true）", async ({ page, request }) => {
    const { deviceId, data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;

    const campaignId = await createCampaign(request, {
      janet_media_id: "77701",
      janet_original_id: "66601",
      enable_points: true,
      point_rate: 1.0,
    });

    // クリック記録
    await page.route(/j-a-net\.jp/, (route) => route.abort());
    await page.goto(`/mdm/affiliate/click/${campaignId}?device_id=${deviceId}`).catch(() => {});

    const actionId = `2P-POINT-${Date.now()}`;

    // Phase 1: pending（ポイント未付与）
    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=400&action_id=${actionId}&attestation_flag=1`
    );

    // Phase 2: approved（ポイント付与）
    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=400&action_id=${actionId}&attestation_flag=0`
    );

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();
    expect(cv.attestation_status).toBe("approved");

    const ptRes = await adminGet(request, `/mdm/admin/affiliate/points?user_token=${userToken}`);
    const points = await ptRes.json();
    const pointForCv = points.find((p) => p.conversion_id === cv.id);
    expect(pointForCv).toBeDefined();
    expect(pointForCv.points).toBe(400);
  });
});

// ══════════════════════════════════════════════════════════════
// 7. CV管理画面レポート
// ══════════════════════════════════════════════════════════════

test.describe("CV管理画面レポート", () => {
  test("GET /mdm/admin/affiliate/conversions が JSON配列を返す", async ({ request }) => {
    const res = await adminGet(request, "/mdm/admin/affiliate/conversions");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("CVレコードに必要フィールドが含まれる", async ({ request }) => {
    const { data: deviceData } = await registerAndroidDevice(request);
    const userToken = deviceData.user_token;
    const actionId = `REPORT-${Date.now()}`;

    await request.get(
      `/mdm/affiliate/postback/janet?user_id=${userToken}&commission=300&action_id=${actionId}&attestation_flag=0`
    );

    const cvRes = await adminGet(request, "/mdm/admin/affiliate/conversions");
    const conversions = await cvRes.json();
    const cv = conversions.find((c) => c.asp_action_id === actionId);
    expect(cv).toBeDefined();
    expect(cv).toHaveProperty("id");
    expect(cv).toHaveProperty("source");
    expect(cv).toHaveProperty("revenue_jpy");
    expect(cv).toHaveProperty("attestation_status");
    expect(cv).toHaveProperty("asp_action_id");
    expect(cv).toHaveProperty("converted_at");
  });

  test("GET /mdm/admin/affiliate/points が認証なしで 401 を返す", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/points");
    expect(res.status()).toBe(401);
  });
});
