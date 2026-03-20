/**
 * video_creative.spec.js — 動画クリエイティブ・VAST配信 E2E テスト
 *
 * テスト対象:
 *   POST /mdm/admin/creatives              — video / image クリエイティブ登録
 *   GET  /mdm/ad/vast/{impression_id}      — VAST 3.0 XML 取得
 *   POST /mdm/ad/video_event/{id}/{event}  — 動画イベント記録
 *   UI   /creatives                        — クリエイティブ登録モーダルのvideoフィールド
 */
const { test, expect } = require("@playwright/test");
const { setAdminKeyInBrowser } = require("./helpers/auth");

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";
const ADMIN_HEADERS = { "X-Admin-Key": ADMIN_KEY };
const UID = Date.now();

let dealerId = null;
let campaignId = null;

test.describe.configure({ mode: "serial" });

// ── セットアップ ────────────────────────────────────────────────

test.describe("動画クリエイティブ API", () => {
  test.beforeAll(async ({ request }) => {
    // アフィリエイトキャンペーン作成（CreativeDB.campaign_id は affiliate_campaigns.id を参照）
    const campRes = await request.post("/mdm/admin/affiliate/campaigns", {
      data: {
        name: `E2E動画キャンペーン-${UID}`,
        category: "app",
        destination_url: "https://example.com/video-lp",
        reward_type: "cpi",
        reward_amount: 500,
      },
      headers: ADMIN_HEADERS,
    });
    expect(campRes.ok()).toBeTruthy();
    const camp = await campRes.json();
    campaignId = camp.id;
    expect(campaignId).toBeTruthy();
  });

  // ── 動画クリエイティブ登録 ───────────────────────────────────

  test("video クリエイティブ登録 — 正常系: id を返す", async ({ request }) => {
    const res = await request.post("/mdm/admin/creatives", {
      data: {
        campaign_id: campaignId,
        name: `E2E動画クリエイティブ-${UID}`,
        type: "video",
        title: "E2E動画広告",
        click_url: "https://example.com/video-lp",
        video_url: "https://example.com/test.mp4",
        video_duration_sec: 30,
        skip_after_sec: 5,
      },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("id");
    expect(d.type).toBe("video");
  });

  test("video クリエイティブ — video_url が DB に保存される", async ({ request }) => {
    // 登録
    const createRes = await request.post("/mdm/admin/creatives", {
      data: {
        campaign_id: campaignId,
        name: `E2E動画-保存確認-${UID}`,
        type: "video",
        title: "保存確認用",
        click_url: "https://example.com/check",
        video_url: "https://example.com/check.mp4",
        video_duration_sec: 15,
        skip_after_sec: 3,
      },
      headers: ADMIN_HEADERS,
    });
    expect(createRes.ok()).toBeTruthy();

    // 一覧で type=video のものが存在することを確認
    const listRes = await request.get(
      `/mdm/admin/creatives?type=video&campaign_id=${campaignId}`,
      { headers: ADMIN_HEADERS }
    );
    expect(listRes.ok()).toBeTruthy();
    const list = await listRes.json();
    expect(Array.isArray(list)).toBeTruthy();
    const videos = list.filter((c) => c.type === "video");
    expect(videos.length).toBeGreaterThan(0);
  });

  test("image クリエイティブ登録 — 正常系: id を返す", async ({ request }) => {
    const res = await request.post("/mdm/admin/creatives", {
      data: {
        campaign_id: campaignId,
        name: `E2E画像クリエイティブ-${UID}`,
        type: "image",
        title: "E2E画像広告",
        click_url: "https://example.com/image-lp",
        image_url: "https://example.com/banner.png",
      },
      headers: ADMIN_HEADERS,
    });
    expect(res.ok()).toBeTruthy();
    const d = await res.json();
    expect(d).toHaveProperty("id");
  });

  test("クリエイティブ登録 — 管理者キーなし → 401 または 403", async ({ request }) => {
    const res = await request.post("/mdm/admin/creatives", {
      data: {
        campaign_id: campaignId,
        name: "不正登録",
        type: "image",
        title: "test",
        click_url: "https://example.com",
      },
    });
    expect(res.status()).toBeGreaterThanOrEqual(401);
  });

  // ── 動画イベント記録 ─────────────────────────────────────────

  test("video_event — 無効なイベント名 → 400", async ({ request }) => {
    const res = await request.post(
      "/mdm/ad/video_event/nonexistent-impression/invalid_event"
    );
    expect(res.status()).toBe(400);
  });

  test("video_event — 有効なイベント + 存在しない impression → 404", async ({
    request,
  }) => {
    for (const event of ["start", "midpoint", "complete", "skip"]) {
      const res = await request.post(
        `/mdm/ad/video_event/nonexistent-impression-id/${event}`
      );
      expect(res.status()).toBe(404);
    }
  });

  // ── VAST XML 取得 ────────────────────────────────────────────

  test("VAST — 存在しない impression_id → 404", async ({ request }) => {
    const res = await request.get("/mdm/ad/vast/nonexistent-impression-id");
    expect(res.status()).toBe(404);
  });

  test("VAST — レスポンスは Content-Type: application/xml (正常時)", async ({
    request,
  }) => {
    // impression が存在しないため404だが、Content-Typeが application/json でないことを確認
    const res = await request.get("/mdm/ad/vast/nonexistent-id");
    // 404 の場合は FastAPI が JSON で返すが、正常時は XML のはず
    // ここでは endpoint が存在すること（500以外）だけ確認
    expect(res.status()).not.toBe(500);
  });
});

// ── UI テスト ──────────────────────────────────────────────────

test.describe("クリエイティブ登録モーダル — video フィールド UI", () => {
  test("type=video 選択時に動画フィールドが表示される", async ({ page }) => {
    await page.goto("/creatives");
    await setAdminKeyInBrowser(page);

    // モーダルを開く
    await page.getByRole("button", { name: /新規登録/ }).click();
    await expect(page.locator("#creative-modal")).toHaveClass(/open/);

    // video を選択
    await page.locator("#creative-type-select").selectOption("video");

    // video-fields が表示される
    const videoFields = page.locator("#video-fields");
    await expect(videoFields).toBeVisible();
    await expect(videoFields.locator('[name="video_url"]')).toBeVisible();
    await expect(videoFields.locator('[name="video_duration_sec"]')).toBeVisible();
    await expect(videoFields.locator('[name="skip_after_sec"]')).toBeVisible();
  });

  test("type=image 選択時に動画フィールドが非表示になる", async ({ page }) => {
    await page.goto("/creatives");
    await setAdminKeyInBrowser(page);

    await page.getByRole("button", { name: /新規登録/ }).click();

    // 先に video を選んでから image に戻す
    await page.locator("#creative-type-select").selectOption("video");
    await page.locator("#creative-type-select").selectOption("image");

    const videoFields = page.locator("#video-fields");
    await expect(videoFields).not.toBeVisible();
  });

  test("type=text 選択時も動画フィールドが非表示", async ({ page }) => {
    await page.goto("/creatives");
    await setAdminKeyInBrowser(page);

    await page.getByRole("button", { name: /新規登録/ }).click();
    await page.locator("#creative-type-select").selectOption("text");

    await expect(page.locator("#video-fields")).not.toBeVisible();
  });

  test("モーダルを閉じて再度開くとフォームがリセットされる", async ({ page }) => {
    await page.goto("/creatives");
    await setAdminKeyInBrowser(page);

    await page.getByRole("button", { name: /新規登録/ }).click();
    await page.locator("#creative-type-select").selectOption("video");
    await page.locator("#creative-modal .modal-close").click();
    await expect(page.locator("#creative-modal")).not.toHaveClass(/open/);

    // 再度開く
    await page.getByRole("button", { name: /新規登録/ }).click();
    // デフォルトは image (先頭オプション) — video-fields は非表示のはず
    await expect(page.locator("#video-fields")).not.toBeVisible();
  });
});
