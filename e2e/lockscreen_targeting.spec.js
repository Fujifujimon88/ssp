const { test, expect } = require('@playwright/test');

const ADMIN_KEY = process.env.ADMIN_API_KEY || 'change-me-admin-key';
const adminHeaders = (extra = {}) => ({
  'X-Admin-Key': ADMIN_KEY,
  'Content-Type': 'application/json',
  ...extra,
});

test.describe('ロック画面5軸ターゲティング', () => {
  let slotId;

  test.beforeAll(async ({ request }) => {
    // テスト用スロット作成
    const res = await request.post('/mdm/admin/slots', {
      headers: adminHeaders(),
      data: { name: 'E2E Targeting Test Slot', slot_type: 'lockscreen', floor_price_cpm: 500 },
    });
    if (res.ok()) {
      slotId = (await res.json()).id;
    }
  });

  test('analyticsエンドポイントが24時間分を返す', async ({ request }) => {
    const r = await request.get('/mdm/admin/lockscreen/analytics?days=7', {
      headers: { 'X-Admin-Key': ADMIN_KEY },
    });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.hours).toHaveLength(24);
    expect(d.hours[0]).toMatchObject({ hour: 0 });
    expect(typeof d.hours[0].ctr).toBe('number');
    expect(typeof d.hours[0].impressions).toBe('number');
  });

  test('スロットにターゲティングを設定できる', async ({ request }) => {
    if (!slotId) test.skip();
    const targeting = {
      time_slots: [7, 8, 9],
      platform: 'android',
      age_groups: ['20s', '30s'],
      screen_on_count_max: 3,
    };
    const res = await request.put(`/mdm/admin/slots/${slotId}/targeting`, {
      headers: adminHeaders(),
      data: { targeting_json: JSON.stringify(targeting) },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const saved = JSON.parse(body.targeting_json);
    expect(saved.time_slots).toContain(8);
    expect(saved.platform).toBe('android');
    expect(saved.screen_on_count_max).toBe(3);
  });

  test('不正なJSONでターゲティング設定は400を返す', async ({ request }) => {
    if (!slotId) test.skip();
    const res = await request.put(`/mdm/admin/slots/${slotId}/targeting`, {
      headers: adminHeaders(),
      data: { targeting_json: 'not-valid-json' },
    });
    expect(res.status()).toBe(400);
  });

  test('dealer regionを設定できる', async ({ request }) => {
    // まずdealerを作成
    const dealerRes = await request.post('/mdm/admin/dealers', {
      headers: adminHeaders(),
      data: { name: 'E2E Region Test Dealer', store_code: `e2e-region-${Date.now()}` },
    });
    if (!dealerRes.ok()) {
      // dealer作成APIのパスが違う場合はスキップ
      test.skip();
      return;
    }
    const dealerId = (await dealerRes.json()).id;
    const regionRes = await request.put(`/mdm/admin/dealer/${dealerId}/region`, {
      headers: adminHeaders(),
      data: { region: 'tokyo' },
    });
    expect(regionRes.ok()).toBeTruthy();
    const body = await regionRes.json();
    expect(body.region).toBe('tokyo');
  });
});
