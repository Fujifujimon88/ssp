/**
 * nanomdm_unconfigured.spec.js
 *
 * 「NanoMDM未設定 → iOS MDM操作のみ失敗、他は正常」の検証
 *
 * Vercel本番環境はNanoMDMが起動していない状態のため、
 * そのままこのスペックで実際の挙動を確認できる。
 *
 * テスト観点:
 *   A. システム全体の健全性 — NanoMDMなしでも /health が ok を返す
 *   B. NanoMDM不要のiOS機能 — 同意API・mobileconfig生成は正常動作
 *   C. NanoMDM依存操作 — 5xx クラッシュなく 4xx でgraceful fail
 *   D. 他システム無影響 — 入札・Android・アフィリエイトは正常動作
 */
const { test, expect } = require("@playwright/test");
const { loadAuth, apiGet, apiPost } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const STORE_CODE = "STORE001";

const ALL_CONSENT_ITEMS = [
  "lockscreen_ads",
  "push_notifications",
  "webclip_install",
  "vpn_setup",
  "app_install",
  "data_collection",
];

// ─────────────────────────────────────────────────────────────
// A. システム全体の健全性
// ─────────────────────────────────────────────────────────────
test.describe("A. システム全体の健全性（NanoMDMなし）", () => {
  test("/health が status:ok を返す", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("ok");
  });

  test("/health に nanomdm フィールドがなくても ok（未設定は正常）", async ({ request }) => {
    const res = await request.get("/health");
    const data = await res.json();
    // nanomdm がなくても status:ok であることを確認
    expect(data.status).toBe("ok");
    // nanomdm キーがある場合は false でも許容（起動していないだけ）
    if ("nanomdm" in data) {
      expect(typeof data.nanomdm).toBe("boolean");
    }
  });
});

// ─────────────────────────────────────────────────────────────
// B. NanoMDM不要のiOS機能 — 正常動作を確認
// ─────────────────────────────────────────────────────────────
test.describe("B. NanoMDM不要のiOS機能（正常動作）", () => {
  test("同意API POST /mdm/device/consent → 200 + mobileconfig_url 返る", async ({ request }) => {
    const res = await request.post("/mdm/device/consent", {
      data: {
        consent_items: ALL_CONSENT_ITEMS,
        age_group: "30s",
        user_agent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
      },
    });
    // NanoMDMを使わないため、正常に200が返る
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("mobileconfig_url");
    expect(data.mobileconfig_url).toContain("/mdm/ios/mobileconfig");
  });

  test("mobileconfig生成 GET /mdm/ios/mobileconfig → 500にならない", async ({ request }) => {
    const res = await request.get(`/mdm/ios/mobileconfig?dealer=${STORE_CODE}&token=dummy-token`);
    // NanoMDMなしでも .mobileconfig の生成自体はサーバーがクラッシュしない
    expect(res.status()).toBeLessThan(500);
  });

  test("iOSデバイス一覧 GET /mdm/admin/ios/devices → 200 + 配列（DBのみ参照）", async ({ request }) => {
    const res = await request.get("/mdm/admin/ios/devices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    // DBから読むだけでNanoMDM不要 → 必ず200が返る
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("エンロールポータル GET /mdm/portal → 200（NanoMDM不要）", async ({ request }) => {
    const res = await request.get(`/mdm/portal?dealer=${STORE_CODE}`);
    expect(res.ok()).toBeTruthy();
  });

  test("MDM管理プロファイル GET /mdm/ios/mobileconfig-mdm → 500にならない", async ({ request }) => {
    // 不正トークン → 4xx が返るが、500にはならない
    const res = await request.get("/mdm/ios/mobileconfig-mdm?token=invalid-token-xyz");
    expect(res.status()).toBeLessThan(500);
    expect([400, 404, 422]).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────
// C. NanoMDM依存操作 — graceful fail（5xx にならない）
// ─────────────────────────────────────────────────────────────
test.describe("C. NanoMDM依存操作のgraceful fail（5xxにならない）", () => {
  test("MDMコマンド送信 POST /mdm/admin/ios/command — 存在しないUDIDは404（サーバー落ちない）", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/command", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        udid: "00000000-NANO-MDM-NOT-RUNNING-0000",
        request_type: "device_info",
        params: {},
        send_push: false,
      },
    });
    // デバイス不在チェックがNanoMDM呼び出し前に行われるため 404
    // 500（サーバークラッシュ）にはならない
    expect(res.status()).toBe(404);
    expect(res.status()).not.toBe(500);
  });

  test("APNs Push POST /mdm/admin/ios/push/{udid} — 存在しないUDIDは404（サーバー落ちない）", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/push/nanomdm-not-running-udid", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.status()).toBe(404);
    expect(res.status()).not.toBe(500);
  });

  test("WebClip広告配信 POST /mdm/admin/ios/push-webclip-ad — 存在しないUDIDは4xx（サーバー落ちない）", async ({ request }) => {
    const res = await request.post("/mdm/admin/ios/push-webclip-ad", {
      headers: { "X-Admin-Key": ADMIN_KEY },
      data: {
        udid: "nanomdm-not-running-udid",
        campaign_id: "nonexistent-campaign",
      },
    });
    // 4xx（device/campaign not found）であり、5xxにならない
    expect(res.status()).toBeLessThan(500);
  });

  test("NanoMDM checkin POST /mdm/ios/checkin — リクエスト受け付け、500にならない", async ({ request }) => {
    // checkinはデバイスからのWebhook受け口
    // 不正なbodyでも500クラッシュしないことを確認
    const res = await request.post("/mdm/ios/checkin", {
      headers: { "Content-Type": "application/xml" },
      data: "<invalid-plist/>",
    });
    // 400/422（バリデーションエラー）は許容、500は不可
    expect(res.status()).toBeLessThan(500);
  });
});

// ─────────────────────────────────────────────────────────────
// D. 他システム無影響（入札・Android・アフィリエイトが正常動作）
// ─────────────────────────────────────────────────────────────
test.describe("D. 他システムへの影響なし（NanoMDMと無関係）", () => {
  test("入札 POST /v1/bid → 正常にbids配列が返る", async ({ request }) => {
    const { publisherId } = loadAuth();
    // スロット一覧からtag_idを取得
    const slotsRes = await apiGet(request, "/api/slots");
    const slots = await slotsRes.json();
    const tagId = Array.isArray(slots) && slots.length > 0 ? slots[0].tag_id : "test-slot";

    const res = await request.post("/v1/bid", {
      data: {
        publisherId,
        slotId: tagId,
        floorPrice: 0.01,
        sizes: [[300, 250]],
      },
    });
    // NanoMDMとは無関係 → 正常動作
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("bids");
  });

  test("Androidデバイス一覧 GET /mdm/admin/android/devices → 200（NanoMDMと無関係）", async ({ request }) => {
    const res = await request.get("/mdm/admin/android/devices", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test("アフィリエイト案件一覧 GET /mdm/admin/affiliate/campaigns → 200（NanoMDMと無関係）", async ({ request }) => {
    const res = await request.get("/mdm/admin/affiliate/campaigns", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
  });

  test("代理店一覧 GET /mdm/admin/agencies → 200（NanoMDMと無関係）", async ({ request }) => {
    const res = await request.get("/mdm/admin/agencies", {
      headers: { "X-Admin-Key": ADMIN_KEY },
    });
    expect(res.ok()).toBeTruthy();
  });
});
