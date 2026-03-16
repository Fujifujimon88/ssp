/**
 * p2_endpoints.spec.js
 * P2 エンドポイント群の E2E テスト
 *
 * カバー範囲:
 *   ADT-02  POST /mdm/game_event                    プレイアブル広告ゲームイベント
 *   ADT-03  POST /openrtb/bid                       OpenRTB インバウンド入札
 *           GET  /openrtb/win/{id}                  Win notice
 *   BKD-11  POST /mdm/admin/agencies                代理店登録
 *           GET  /mdm/admin/agencies                代理店一覧
 *           GET  /mdm/agency/devices                代理店デバイス一覧
 *           GET  /mdm/agency/revenue                代理店月次収益
 *   BKD-12  POST /mdm/admin/settlement/run          月次精算実行
 *           GET  /mdm/admin/settlement/invoices     精算一覧
 *   ML-02   POST /mdm/admin/ml/train                Two-Tower 学習トリガー
 *           GET  /mdm/admin/ml/models               学習済みモデル一覧
 *   ML-03   POST /mdm/admin/ml/compute_cohorts      コホートセグメント計算
 *           GET  /mdm/admin/ml/cohort_stats         コホート統計
 */
const { test, expect } = require("@playwright/test");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";

// ════════════════════════════════════════════════════════════════════
// ADT-02 — プレイアブル広告ゲームイベント
// ════════════════════════════════════════════════════════════════════

test.describe("ADT-02 — プレイアブル広告ゲームイベント POST /mdm/game_event", () => {
  test("game_start イベントを記録できる（印象IDなし）", async ({ request }) => {
    const res = await request.post("/mdm/game_event", {
      data: {
        event: "game_start",
        impression_id: "",
        device_id: "test-device-e2e-001",
      },
    });
    // impression が存在しなくても 200 を返す（ログのみ）
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
  });

  test("game_complete イベントを記録できる", async ({ request }) => {
    const res = await request.post("/mdm/game_event", {
      data: {
        event: "game_complete",
        impression_id: "test-imp-e2e-001",
        device_id: "test-device-e2e-001",
        score: 1500,
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
  });

  test("game_converted イベントを記録できる", async ({ request }) => {
    const res = await request.post("/mdm/game_event", {
      data: {
        event: "game_converted",
        impression_id: "test-imp-e2e-002",
        device_id: "test-device-e2e-001",
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
  });

  test("不正なイベント名は 400 が返る", async ({ request }) => {
    const res = await request.post("/mdm/game_event", {
      data: {
        event: "invalid_event_xyz",
        impression_id: "x",
        device_id: "y",
      },
    });
    expect(res.status()).toBe(400);
  });

  test("event フィールド空文字は 400 が返る", async ({ request }) => {
    const res = await request.post("/mdm/game_event", {
      data: {
        event: "",
        impression_id: "test-imp",
        device_id: "test-device",
      },
    });
    expect(res.status()).toBe(400);
  });
});

// ════════════════════════════════════════════════════════════════════
// ADT-03 — OpenRTB インバウンド入札
// ════════════════════════════════════════════════════════════════════

test.describe("ADT-03 — OpenRTB 入札 POST /openrtb/bid", () => {
  test("有効な DSP APIキーで入札が通る", async ({ request }) => {
    const bidRequest = {
      id: `test-bid-${Date.now()}`,
      imp: [
        {
          id: "imp-001",
          banner: { w: 320, h: 480 },
          bidfloor: 100.0,
          bidfloorcur: "JPY",
        },
      ],
      app: { id: "test-app", bundle: "jp.test.app" },
    };

    const res = await request.post("/openrtb/bid", {
      headers: {
        "x-openrtb-apikey": "test-dsp-key-1",
        "Content-Type": "application/json",
      },
      data: bidRequest,
    });

    // 200 または no-bid でも 200 が返る（nbr フィールドで区別）
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("id");
  });

  test("APIキーなしで 401 が返る", async ({ request }) => {
    const res = await request.post("/openrtb/bid", {
      headers: { "Content-Type": "application/json" },
      data: {
        id: "test-no-key",
        imp: [{ id: "imp-001", banner: { w: 320, h: 480 }, bidfloor: 100.0 }],
      },
    });
    expect(res.status()).toBe(401);
  });

  test("不正な APIキーで 401 が返る", async ({ request }) => {
    const res = await request.post("/openrtb/bid", {
      headers: {
        "x-openrtb-apikey": "invalid-key-xyz",
        "Content-Type": "application/json",
      },
      data: {
        id: "test-bad-key",
        imp: [{ id: "imp-001", banner: { w: 320, h: 480 }, bidfloor: 100.0 }],
      },
    });
    expect(res.status()).toBe(401);
  });

  test("2つ目の許可済み DSP キーでも入札が通る", async ({ request }) => {
    const res = await request.post("/openrtb/bid", {
      headers: {
        "x-openrtb-apikey": "test-dsp-key-2",
        "Content-Type": "application/json",
      },
      data: {
        id: `test-bid2-${Date.now()}`,
        imp: [
          {
            id: "imp-002",
            banner: { w: 300, h: 250 },
            bidfloor: 500.0,
            bidfloorcur: "JPY",
          },
        ],
      },
    });
    expect(res.ok()).toBeTruthy();
  });
});

test.describe("ADT-03 — OpenRTB Win Notice GET /openrtb/win/{id}", () => {
  test("任意のオークションIDで win notice を受け付ける", async ({ request }) => {
    const auctionId = `auction-${Date.now()}`;
    const res = await request.get(
      `/openrtb/win/${auctionId}?price=500.0&imp_id=imp-001`
    );
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
  });

  test("price=0 でも win notice が成功する", async ({ request }) => {
    const res = await request.get(`/openrtb/win/auction-zero-price?price=0`);
    expect(res.ok()).toBeTruthy();
  });
});

// ════════════════════════════════════════════════════════════════════
// BKD-11 — 代理店ポータル API
// ════════════════════════════════════════════════════════════════════

test.describe("BKD-11 — 代理店登録 POST /mdm/admin/agencies", () => {
  test("管理者キーで代理店を登録できる", async ({ request }) => {
    const agencyName = `E2Eテスト代理店_${Date.now()}`;
    const res = await request.post("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: {
        name: agencyName,
        contact_email: `e2e-agency-${Date.now()}@example.com`,
      },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("id");
    expect(body).toHaveProperty("name");
    expect(body).toHaveProperty("api_key");
    expect(body.name).toBe(agencyName);
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      headers: { "Content-Type": "application/json" },
      data: { name: "不正代理店" },
    });
    expect(res.status()).toBe(401);
  });

  test("name フィールドなしで 400 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { contact_email: "noname@example.com" },
    });
    expect(res.status()).toBe(400);
  });
});

test.describe("BKD-11 — 代理店一覧 GET /mdm/admin/agencies", () => {
  test("管理者キーで代理店一覧が取得できる", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("agencies");
    expect(Array.isArray(body.agencies)).toBeTruthy();
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies");
    expect(res.status()).toBe(401);
  });

  test("登録した代理店が一覧に含まれる", async ({ request }) => {
    // まず代理店を登録
    const agencyName = `一覧確認代理店_${Date.now()}`;
    const createRes = await request.post("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { name: agencyName, contact_email: "list-check@example.com" },
    });
    expect(createRes.ok()).toBeTruthy();

    // 一覧に含まれていることを確認
    const listRes = await request.get("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(listRes.ok()).toBeTruthy();
    const body = await listRes.json();
    expect(body.agencies.some((a) => a.name === agencyName)).toBeTruthy();
  });
});

test.describe("BKD-11 — 代理店デバイス一覧 GET /mdm/agency/devices", () => {
  // テスト前に代理店を作成して api_key を取得する
  let agencyApiKey = "";

  test.beforeAll(async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: {
        name: `デバイス一覧テスト代理店_${Date.now()}`,
        contact_email: "devices-test@example.com",
      },
    });
    if (res.ok()) {
      const body = await res.json();
      agencyApiKey = body.api_key;
    }
  });

  test("有効な代理店キーでデバイス一覧が取得できる", async ({ request }) => {
    if (!agencyApiKey) test.skip();
    const res = await request.get("/mdm/agency/devices", {
      headers: { "X-Agency-Key": agencyApiKey },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("agency");
    expect(body).toHaveProperty("android_count");
    expect(body).toHaveProperty("ios_count");
  });

  test("代理店キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/agency/devices");
    expect(res.status()).toBe(401);
  });

  test("不正な代理店キーで 403 が返る", async ({ request }) => {
    const res = await request.get("/mdm/agency/devices", {
      headers: { "X-Agency-Key": "totally-invalid-key-xyz" },
    });
    expect(res.status()).toBe(403);
  });
});

test.describe("BKD-11 — 代理店月次収益 GET /mdm/agency/revenue", () => {
  let agencyApiKey = "";

  test.beforeAll(async ({ request }) => {
    const res = await request.post("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: {
        name: `収益テスト代理店_${Date.now()}`,
        contact_email: "revenue-test@example.com",
      },
    });
    if (res.ok()) {
      const body = await res.json();
      agencyApiKey = body.api_key;
    }
  });

  test("有効な代理店キーで収益レポートが取得できる", async ({ request }) => {
    if (!agencyApiKey) test.skip();
    const res = await request.get("/mdm/agency/revenue", {
      headers: { "X-Agency-Key": agencyApiKey },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("agency");
    expect(body).toHaveProperty("period_month");
    expect(body).toHaveProperty("gross_revenue_jpy");
    expect(body).toHaveProperty("net_payable_jpy");
  });

  test("month クエリパラメータで特定月を指定できる", async ({ request }) => {
    if (!agencyApiKey) test.skip();
    const res = await request.get("/mdm/agency/revenue?month=2026-03", {
      headers: { "X-Agency-Key": agencyApiKey },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.period_month).toBe("2026-03");
  });

  test("代理店キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/agency/revenue");
    expect(res.status()).toBe(401);
  });
});

// ════════════════════════════════════════════════════════════════════
// BKD-12 — 収益自動精算エンジン
// ════════════════════════════════════════════════════════════════════

test.describe("BKD-12 — 月次精算実行 POST /mdm/admin/settlement/run", () => {
  test("管理者キーで精算を開始できる", async ({ request }) => {
    const res = await request.post("/mdm/admin/settlement/run", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: { period_month: "2026-02" },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("started");
    expect(body).toHaveProperty("period_month");
  });

  test("period_month 省略時は前月が自動設定される", async ({ request }) => {
    const res = await request.post("/mdm/admin/settlement/run", {
      headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json" },
      data: {},
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("started");
    // period_month が YYYY-MM 形式であることを確認
    expect(body.period_month).toMatch(/^\d{4}-\d{2}$/);
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/settlement/run", {
      headers: { "Content-Type": "application/json" },
      data: { period_month: "2026-02" },
    });
    expect(res.status()).toBe(401);
  });
});

test.describe("BKD-12 — 精算一覧 GET /mdm/admin/settlement/invoices", () => {
  test("管理者キーで精算一覧が取得できる", async ({ request }) => {
    const res = await request.get("/mdm/admin/settlement/invoices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("invoices");
    expect(Array.isArray(body.invoices)).toBeTruthy();
  });

  test("period_month フィルタが動作する", async ({ request }) => {
    const res = await request.get(
      "/mdm/admin/settlement/invoices?period_month=2026-01",
      { headers: { "X-Admin-Key": ADMIN_KEY } }
    );
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("invoices");
    // フィルタされた結果は全て 2026-01 のもの
    for (const inv of body.invoices) {
      expect(inv.period_month).toBe("2026-01");
    }
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/settlement/invoices");
    expect(res.status()).toBe(401);
  });
});

// ════════════════════════════════════════════════════════════════════
// ML-02 — Two-Tower モデル学習
// ════════════════════════════════════════════════════════════════════

test.describe("ML-02 — モデル学習トリガー POST /mdm/admin/ml/train", () => {
  test("管理者キーで学習をキューできる", async ({ request }) => {
    const res = await request.post("/mdm/admin/ml/train", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("started");
    expect(body).toHaveProperty("message");
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ml/train");
    expect(res.status()).toBe(401);
  });
});

test.describe("ML-02 — 学習済みモデル一覧 GET /mdm/admin/ml/models", () => {
  test("管理者キーでモデル一覧が取得できる", async ({ request }) => {
    const res = await request.get("/mdm/admin/ml/models", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("models");
    expect(Array.isArray(body.models)).toBeTruthy();
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/ml/models");
    expect(res.status()).toBe(401);
  });
});

// ════════════════════════════════════════════════════════════════════
// ML-03 — 行動コホートセグメント
// ════════════════════════════════════════════════════════════════════

test.describe("ML-03 — コホート計算 POST /mdm/admin/ml/compute_cohorts", () => {
  test("管理者キーでコホート計算をキューできる", async ({ request }) => {
    const res = await request.post("/mdm/admin/ml/compute_cohorts", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("started");
    expect(body).toHaveProperty("message");
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.post("/mdm/admin/ml/compute_cohorts");
    expect(res.status()).toBe(401);
  });
});

test.describe("ML-03 — コホート統計 GET /mdm/admin/ml/cohort_stats", () => {
  test("管理者キーでコホート統計が取得できる", async ({ request }) => {
    const res = await request.get("/mdm/admin/ml/cohort_stats", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("cohorts");
    expect(Array.isArray(body.cohorts)).toBeTruthy();
  });

  test("コホートが存在する場合は各フィールドを持つ", async ({ request }) => {
    const res = await request.get("/mdm/admin/ml/cohort_stats", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    for (const cohort of body.cohorts) {
      expect(cohort).toHaveProperty("cohort_id");
      expect(cohort).toHaveProperty("device_count");
    }
  });

  test("管理者キーなしで 401 が返る", async ({ request }) => {
    const res = await request.get("/mdm/admin/ml/cohort_stats");
    expect(res.status()).toBe(401);
  });
});
