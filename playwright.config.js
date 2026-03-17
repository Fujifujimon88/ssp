const { defineConfig, devices } = require("@playwright/test");
require("dotenv").config({ path: ".env.test" });

module.exports = defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.js",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.BASE_URL || "http://127.0.0.1:8000",
    headless: true,
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    locale: "ja-JP",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
