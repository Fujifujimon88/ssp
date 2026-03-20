const { test, expect } = require("@playwright/test");
const { setAdminKeyInBrowser } = require("./helpers/auth");

test("代理店UI登録デバッグ", async ({ page }) => {
  page.on('console', msg => console.log('[PAGE]', msg.type(), msg.text()));
  page.on('pageerror', err => console.log('[PGERR]', err.message));
  
  await page.goto("/agencies");
  await setAdminKeyInBrowser(page);
  
  const requests = [];
  page.on('request', r => { if (r.url().includes('agencies')) requests.push('REQ:'+r.method()+' '+r.url()); });
  page.on('response', r => { if (r.url().includes('agencies')) r.text().then(t => requests.push('RES:'+r.status()+' '+r.url()+' '+t.substring(0,100))); });
  
  const newName = `Debug-${Date.now()}`;
  await page.locator("button[onclick='showAgencyModal()']").click();
  await page.waitForSelector("#agency-modal.open", { timeout: 3000 });
  
  await page.locator("#agency-form [name=name]").fill(newName);
  await page.locator("#agency-form [name=contact_email]").fill("debug@example.com");
  
  const key = await page.evaluate(() => localStorage.getItem('ssp_admin_key'));
  console.log("ADMINKEY:", key ? key.substring(0,8)+'...' : 'NONE');
  
  await page.locator("#agency-form button[type=submit]").click();
  console.log("SUBMITTED");
  
  await page.waitForTimeout(2000);
  const resultText = await page.locator("#agency-result").textContent();
  console.log("RESULT:", JSON.stringify(resultText));
  console.log("REQUESTS:", requests.join(' | '));
});
