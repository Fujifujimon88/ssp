"""CPI課金自動トリガー テスト"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── finalize_billing 関数の直接テスト ───────────────────────────────────


class TestFinalizeBilling:
    """finalize_billing() の純粋ロジックテスト"""

    def _make_event(self, billing_status="pending", postback_status="pending", hours_ago=1):
        event = MagicMock()
        event.id = "test-event-id"
        event.billing_status = billing_status
        event.postback_status = postback_status
        event.cpi_amount = 400.0
        event.campaign_id = "test-campaign-id"
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        event.created_at = ts
        return event

    def _make_invoice(self, cpi_count=0, gross=0):
        inv = MagicMock()
        inv.cpi_count = cpi_count
        inv.gross_revenue_jpy = gross
        inv.take_rate = 0.175
        inv.platform_fee_jpy = int(gross * 0.175)
        inv.net_payable_jpy = gross - int(gross * 0.175)
        return inv

    @pytest.mark.asyncio
    async def test_postback_success_transitions_to_billable(self):
        """ポストバック成功 → billable に遷移"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(billing_status="pending", postback_status="success")
        invoice = self._make_invoice()

        db = AsyncMock()
        db.get.side_effect = [event, MagicMock()]  # event, campaign
        db.scalar.return_value = invoice

        await finalize_billing(db, "test-event-id")

        assert event.billing_status == "billable"
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_already_billable_is_idempotent(self):
        """既にbillable → 何もしない（冪等）"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(billing_status="billable", postback_status="success")
        db = AsyncMock()
        db.get.return_value = event

        await finalize_billing(db, "test-event-id")

        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_paid_is_idempotent(self):
        """既にpaid → 何もしない（冪等）"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(billing_status="paid", postback_status="success")
        db = AsyncMock()
        db.get.return_value = event

        await finalize_billing(db, "test-event-id")

        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_postback_failed_within_48h_stays_pending(self):
        """ポストバック失敗 + 24h以内 → pendingのまま"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(billing_status="pending", postback_status="failed", hours_ago=24)
        db = AsyncMock()
        db.get.return_value = event

        await finalize_billing(db, "test-event-id")

        assert event.billing_status == "pending"
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_postback_failed_after_48h_transitions_to_billable(self):
        """ポストバック失敗 + 48h以上経過 → billableに遷移"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(billing_status="pending", postback_status="failed", hours_ago=49)
        invoice = self._make_invoice()

        db = AsyncMock()
        db.get.side_effect = [event, MagicMock()]
        db.scalar.return_value = invoice

        await finalize_billing(db, "test-event-id")

        assert event.billing_status == "billable"
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoice_cpi_count_incremented(self):
        """InvoiceDB の既存レコードにアトミック加算（db.execute が呼ばれる）"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(postback_status="success")
        invoice = self._make_invoice(cpi_count=5, gross=2000)
        invoice.id = "test-invoice-id"

        db = AsyncMock()
        db.get.side_effect = [event, MagicMock()]
        db.scalar.return_value = invoice

        await finalize_billing(db, "test-event-id")

        # アトミック更新のため db.execute が呼ばれる（in-memory変更ではない）
        db.execute.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_invoice_created_when_none_exists(self):
        """当月のInvoiceDBがない場合 → 新規作成"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        event = self._make_event(postback_status="success")
        campaign = MagicMock()
        campaign.agency_id = "agency-1"

        db = AsyncMock()
        db.get.side_effect = [event, campaign]
        db.scalar.return_value = None  # invoice not found

        await finalize_billing(db, "test-event-id")

        db.add.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_not_found_does_not_raise(self):
        """install_event_id が存在しない場合 → 例外を投げない"""
        from mdm.router import _finalize_billing_impl as finalize_billing

        db = AsyncMock()
        db.get.return_value = None

        # 例外が発生しないことを確認
        await finalize_billing(db, "nonexistent-id")
        db.commit.assert_not_called()


# ─── APIエンドポイントテスト (conftest.pyのclientを使用) ──────────────────


class TestBillingEndpoints:
    """billing管理エンドポイントのHTTPテスト"""

    @pytest.mark.asyncio
    async def test_billing_pending_list_requires_admin_key(self, client):
        """admin key なしで401"""
        r = await client.get("/mdm/admin/billing/pending")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_billing_pending_list_returns_list(self, client, admin_key):
        """pending一覧が返る"""
        r = await client.get(
            "/mdm/admin/billing/pending",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_billing_invoice_invalid_period(self, client, admin_key):
        """不正なperiodで400"""
        r = await client.get(
            "/mdm/admin/billing/invoice/invalid-period",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_billing_invoice_valid_period(self, client, admin_key):
        """正しいperiodでレスポンスが返る"""
        r = await client.get(
            "/mdm/admin/billing/invoice/2026-03",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 200
        body = r.json()
        assert "invoices" in body
        assert "total_gross_jpy" in body
        assert "total_cpi_count" in body

    @pytest.mark.asyncio
    async def test_mark_paid_not_found(self, client, admin_key):
        """存在しないinstall_event_id → 404"""
        r = await client.post(
            "/mdm/admin/billing/mark-paid/nonexistent-id",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_mark_paid_pending_returns_409(self, client, admin_key):
        """billing_status=pendingのイベントをpaidにしようとすると409"""
        # このテストはInstallEventDBにpendingなレコードが必要
        pytest.skip("requires seed data")
