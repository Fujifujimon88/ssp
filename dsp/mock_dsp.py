"""
開発・テスト用モックDSP
- 実際のDSP接続なしにオークションをローカルで動かせる
- 複数インスタンス起動することで競合入札を再現
- 遅延・NoBidのシミュレーションも可能
"""
import asyncio
import random
import uuid
from typing import Optional

from auction.openrtb import BidRequest, BidResponse, Bid, SeatBid
from dsp.base import BaseDSP


class MockDSP(BaseDSP):
    """
    設定可能なモックDSP。

    Args:
        dsp_id:      DSP識別子
        name:        DSP名
        base_cpm:    入札の基準CPM（ランダム変動あり）
        win_rate:    入札参加率 0.0〜1.0（0.8=80%の確率で入札）
        latency_ms:  応答遅延(ms)。80ms超でタイムアウト対象になる
    """

    def __init__(
        self,
        dsp_id: str,
        name: str,
        base_cpm: float = 1.0,
        win_rate: float = 0.8,
        latency_ms: int = 20,
    ):
        super().__init__(dsp_id=dsp_id, name=name, endpoint="mock://localhost")
        self.base_cpm = base_cpm
        self.win_rate = win_rate
        self.latency_ms = latency_ms

    async def send_bid_request(self, bid_request: BidRequest) -> Optional[BidResponse]:
        # 遅延シミュレーション
        await asyncio.sleep(self.latency_ms / 1000)

        # No-bid シミュレーション
        if random.random() > self.win_rate:
            return BidResponse(id=bid_request.id, nbr=2)  # nbr=2: No bid

        bids = []
        for imp in bid_request.imp:
            # CPMにランダムな変動を加える
            price = self.base_cpm * random.uniform(0.7, 1.3)

            # フロアプライスを下回ったら入札しない
            if price < imp.bidfloor:
                continue

            bids.append(
                Bid(
                    id=str(uuid.uuid4()),
                    impid=imp.id,
                    price=round(price, 4),
                    adm=self._generate_ad_markup(imp),
                    crid=f"creative-{self.dsp_id}-{uuid.uuid4().hex[:8]}",
                    adomain=[f"{self.name.lower().replace(' ', '')}.example.com"],
                    w=imp.banner.w if imp.banner else None,
                    h=imp.banner.h if imp.banner else None,
                )
            )

        if not bids:
            return BidResponse(id=bid_request.id, nbr=3)  # nbr=3: floor未達

        return BidResponse(
            id=bid_request.id,
            seatbid=[SeatBid(bid=bids, seat=self.dsp_id)],
            cur="USD",
        )

    def _generate_ad_markup(self, imp) -> str:
        w = imp.banner.w if imp.banner else 300
        h = imp.banner.h if imp.banner else 250
        return (
            f'<div style="width:{w}px;height:{h}px;background:#e8f4fd;'
            f'display:flex;align-items:center;justify-content:center;'
            f'border:1px solid #bbd;">'
            f'<span style="color:#333;font-size:14px;">[Ad by {self.name}]</span>'
            f'</div>'
        )


def create_mock_dsps() -> list[MockDSP]:
    """開発用のモックDSPセットを返す"""
    return [
        MockDSP(dsp_id="mock-ttd",   name="Mock TradeDesk",  base_cpm=2.5, win_rate=0.9, latency_ms=15),
        MockDSP(dsp_id="mock-xandr", name="Mock Xandr",      base_cpm=2.0, win_rate=0.85, latency_ms=25),
        MockDSP(dsp_id="mock-dv360", name="Mock DV360",      base_cpm=3.0, win_rate=0.7, latency_ms=35),
        MockDSP(dsp_id="mock-low",   name="Mock LowBidder",  base_cpm=0.8, win_rate=0.95, latency_ms=10),
        MockDSP(dsp_id="mock-slow",  name="Mock SlowDSP",    base_cpm=5.0, win_rate=0.6, latency_ms=90),  # タイムアウト対象
    ]
