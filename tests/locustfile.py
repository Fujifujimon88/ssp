"""
Locust 負荷テスト
実行: docker compose --profile loadtest up
または: locust -f tests/locustfile.py --host http://localhost:8000

ブラウザで http://localhost:8089 を開いてテスト開始
"""
import json
import random
import uuid

from locust import HttpUser, between, task


PUBLISHER_IDS = [str(uuid.uuid4()) for _ in range(5)]
SLOT_IDS = [uuid.uuid4().hex[:16] for _ in range(10)]
SIZES = [[300, 250], [728, 90], [320, 50], [160, 600]]


class SSPUser(HttpUser):
    """
    典型的なパブリッシャーサイト訪問者がアクセスしたときの
    ヘッダービディング入札リクエストをシミュレート。
    """
    wait_time = between(0.1, 0.5)

    @task(10)
    def bid_request(self):
        """メインユースケース: Prebid.jsからの入札リクエスト"""
        size = random.choice(SIZES)
        payload = {
            "publisherId": random.choice(PUBLISHER_IDS),
            "slotId": random.choice(SLOT_IDS),
            "floorPrice": round(random.uniform(0.3, 1.5), 2),
            "sizes": [size],
            "pageUrl": f"https://example.com/article/{random.randint(1, 10000)}",
            "referer": "https://www.google.com/",
        }
        with self.client.post(
            "/v1/bid",
            json=payload,
            catch_response=True,
            name="/v1/bid",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                bids = data.get("bids", [])
                if bids and bids[0].get("cpm", 0) > 0:
                    resp.success()
                else:
                    resp.success()  # no-bid も正常
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def health_check(self):
        """ヘルスチェック（モニタリング相当）"""
        with self.client.get("/health", catch_response=True, name="/health") as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure("Health check failed")


class PublisherAPIUser(HttpUser):
    """パブリッシャーが管理画面を操作するシナリオ"""
    wait_time = between(1, 3)

    def on_start(self):
        # テスト用登録
        r = self.client.post(
            "/auth/register",
            params={"password": "testpass123"},
            json={
                "name": f"テストサイト{random.randint(1,999)}",
                "domain": f"test{random.randint(1,99999)}.example.com",
                "contact_email": "test@example.com",
                "floor_price": 0.5,
            },
        )
        if r.status_code == 200:
            self.token = r.json().get("access_token", "")
        else:
            self.token = ""

    @task(3)
    def get_slots(self):
        if not self.token:
            return
        self.client.get(
            "/api/slots",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/api/slots",
        )

    @task(1)
    def get_report(self):
        if not self.token:
            return
        self.client.get(
            "/api/reports/daily",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/api/reports/daily",
        )
