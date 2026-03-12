"""
SSPオークションエンジン
- Second-price auction (Vickrey auction)
- asyncio.wait による並列DSP入札（タイムアウト安全）
- 80ms タイムアウト強制
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from auction.openrtb import BidRequest, BidResponse, Bid, SeatBid, WinNotice

logger = logging.getLogger(__name__)

AUCTION_TIMEOUT_SEC = 0.08  # 80ms


@dataclass
class BidResult:
    dsp_id: str
    bid: Bid
    response_time_ms: float


@dataclass
class AuctionResult:
    auction_id: str
    imp_id: str
    winner: Optional[BidResult]
    clearing_price: float
    all_bids: list[BidResult]
    duration_ms: float


class AuctionEngine:
    def __init__(self):
        self._dsps: dict = {}

    def register_dsp(self, dsp_id: str, dsp_instance) -> None:
        self._dsps[dsp_id] = dsp_instance
        logger.info(f"DSP registered: {dsp_id}")

    async def run_auction(self, bid_request: BidRequest) -> list[AuctionResult]:
        results = []
        for imp in bid_request.imp:
            result = await self._run_single_auction(bid_request, imp)
            results.append(result)
        return results

    async def _run_single_auction(self, bid_request: BidRequest, imp) -> AuctionResult:
        start = time.monotonic()

        if not self._dsps:
            return AuctionResult(
                auction_id=bid_request.id,
                imp_id=imp.id,
                winner=None,
                clearing_price=0.0,
                all_bids=[],
                duration_ms=0.0,
            )

        tasks = {
            dsp_id: asyncio.create_task(
                self._fetch_bid(dsp_id, dsp, bid_request, imp)
            )
            for dsp_id, dsp in self._dsps.items()
        }

        # asyncio.wait を使う: done/pending を明示的に扱い CancelledError を回避
        done, pending = await asyncio.wait(
            list(tasks.values()),
            timeout=AUCTION_TIMEOUT_SEC,
        )

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(f"Auction timeout: {len(pending)} DSP(s) slow for imp {imp.id}")

        task_to_dsp = {v: k for k, v in tasks.items()}

        bid_results: list[BidResult] = []
        for task in done:
            dsp_id = task_to_dsp[task]
            if task.cancelled():
                continue
            exc = task.exception()
            if exc:
                logger.warning(f"DSP {dsp_id} error: {exc}")
                continue
            result = task.result()
            if result:
                bid_results.append(result)

        duration_ms = (time.monotonic() - start) * 1000

        valid_bids = [b for b in bid_results if b.bid.price >= imp.bidfloor]

        if not valid_bids:
            return AuctionResult(
                auction_id=bid_request.id,
                imp_id=imp.id,
                winner=None,
                clearing_price=0.0,
                all_bids=bid_results,
                duration_ms=duration_ms,
            )

        valid_bids.sort(key=lambda x: x.bid.price, reverse=True)
        winner = valid_bids[0]

        if len(valid_bids) >= 2:
            clearing_price = valid_bids[1].bid.price
        else:
            clearing_price = max(imp.bidfloor, winner.bid.price * 0.85)

        logger.info(
            f"Auction done | imp={imp.id} | winner={winner.dsp_id} "
            f"| bid={winner.bid.price:.3f} | clear={clearing_price:.3f} "
            f"| duration={duration_ms:.1f}ms"
        )

        return AuctionResult(
            auction_id=bid_request.id,
            imp_id=imp.id,
            winner=winner,
            clearing_price=clearing_price,
            all_bids=bid_results,
            duration_ms=duration_ms,
        )

    async def _fetch_bid(self, dsp_id: str, dsp, bid_request: BidRequest, imp) -> Optional[BidResult]:
        start = time.monotonic()
        try:
            response: Optional[BidResponse] = await dsp.send_bid_request(bid_request)
            if not response or not response.seatbid:
                return None
            for seatbid in response.seatbid:
                for bid in seatbid.bid:
                    if bid.impid == imp.id and bid.price > 0:
                        elapsed = (time.monotonic() - start) * 1000
                        return BidResult(dsp_id=dsp_id, bid=bid, response_time_ms=elapsed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"DSP {dsp_id} bid failed: {e}")
        return None

    def build_win_notice(self, result: AuctionResult) -> Optional[WinNotice]:
        if not result.winner:
            return None
        return WinNotice(
            auction_id=result.auction_id,
            imp_id=result.imp_id,
            winning_price=result.clearing_price,
            dsp_id=result.winner.dsp_id,
            creative_id=result.winner.bid.crid,
        )
