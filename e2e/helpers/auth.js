/**
 * auth.js - 認証ヘルパー
 * global-setup.js が保存した user.json からトークンを読み込む。
 */
const fs = require("fs");
const path = require("path");

const AUTH_FILE = path.join(__dirname, "../.auth/user.json");

/**
 * 保存済みの認証情報を返す。
 * @returns {{ token: string, publisherId: string, domain: string }}
 */
function loadAuth() {
  if (!fs.existsSync(AUTH_FILE)) {
    throw new Error(
      `[auth] ${AUTH_FILE} が見つかりません。先に global-setup を実行してください。`
    );
  }
  return JSON.parse(fs.readFileSync(AUTH_FILE, "utf-8"));
}

/**
 * Playwright の request コンテキストに Bearer トークンを付与する。
 * @param {import('@playwright/test').APIRequestContext} request
 * @param {string} endpoint
 * @param {object} options
 */
async function apiGet(request, endpoint, options = {}) {
  const { token } = loadAuth();
  return request.get(endpoint, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) },
  });
}

async function apiPost(request, endpoint, data, options = {}) {
  const { token } = loadAuth();
  return request.post(endpoint, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) },
    data,
  });
}

const ADMIN_KEY = process.env.ADMIN_API_KEY || "change-me-admin-key";

async function adminGet(request, endpoint, options = {}) {
  return request.get(endpoint, {
    ...options,
    headers: { "X-Admin-Key": ADMIN_KEY, ...(options.headers || {}) },
  });
}

async function adminPut(request, endpoint, options = {}) {
  return request.put(endpoint, {
    ...options,
    headers: { "X-Admin-Key": ADMIN_KEY, ...(options.headers || {}) },
  });
}

/**
 * 管理者キーをブラウザのlocalStorageにセットしてページをリロードする。
 * @param {import('@playwright/test').Page} page
 */
async function setAdminKeyInBrowser(page) {
  await page.evaluate((key) => localStorage.setItem("ssp_admin_key", key), ADMIN_KEY);
  await page.reload();
  await page.waitForLoadState("domcontentloaded");
}

module.exports = { loadAuth, apiGet, apiPost, adminGet, adminPut, setAdminKeyInBrowser };
