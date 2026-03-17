/**
 * global-setup.js
 * テスト用パブリッシャーをAPIで登録/ログインし、JWTトークンを
 * e2e/.auth/user.json に保存する（各テストで再利用）。
 */
const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");

const BASE_URL = process.env.BASE_URL || "http://localhost:8000";
const TEST_DOMAIN = process.env.TEST_DOMAIN || "e2e-test.example.com";
const TEST_PASSWORD = process.env.TEST_PASSWORD || "e2eTestPass123";
const AUTH_FILE = path.join(__dirname, ".auth", "user.json");

async function fetchWithRetry(url, options = {}, retries = 3, delayMs = 2000) {
  for (let i = 0; i < retries; i++) {
    const res = await fetch(url, options);
    if (res.status !== 500 && res.status !== 502 && res.status !== 503) return res;
    console.log(`[global-setup] ${res.status} → リトライ ${i + 1}/${retries}`);
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return fetch(url, options);
}

async function globalSetup() {
  // --- 既存のテスト用パブリッシャーにログイン試行 ---
  let token = null;
  let publisherId = null;

  const loginRes = await fetchWithRetry(`${BASE_URL}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      username: TEST_DOMAIN,
      password: TEST_PASSWORD,
    }),
  });

  if (loginRes.ok) {
    const data = await loginRes.json();
    token = data.access_token;
    console.log("[global-setup] 既存パブリッシャーでログイン成功");
  } else {
    // --- 存在しなければ新規登録 ---
    const registerRes = await fetchWithRetry(
      `${BASE_URL}/auth/register?password=${encodeURIComponent(TEST_PASSWORD)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "E2E Test Publisher",
          domain: TEST_DOMAIN,
          contact_email: "e2e@example.com",
          floor_price: 0.5,
        }),
      }
    );

    if (!registerRes.ok) {
      const err = await registerRes.text();
      throw new Error(`[global-setup] パブリッシャー登録失敗: ${err}`);
    }

    const data = await registerRes.json();
    token = data.access_token;
    publisherId = data.publisher_id;
    console.log("[global-setup] テスト用パブリッシャー登録完了:", publisherId);
  }

  // JWTトークンを取得してから publisherId を取得
  if (!publisherId) {
    const meRes = await fetch(`${BASE_URL}/api/publishers/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (meRes.ok) {
      const me = await meRes.json();
      publisherId = me.id;
    }
  }

  // .auth/user.json に保存
  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  fs.writeFileSync(
    AUTH_FILE,
    JSON.stringify({ token, publisherId, domain: TEST_DOMAIN }, null, 2)
  );
  console.log("[global-setup] 認証情報を保存しました:", AUTH_FILE);
}

module.exports = globalSetup;
