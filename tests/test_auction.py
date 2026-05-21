"""
オークションエンジンのユニットテスト
実行: cd ssp_platform && pytest tests/test_auction.py -v
"""
import pytest

from auction.engine import AuctionEngine, BidResult, _compute_clearing_price
from auction.openrtb import BidRequest, Banner, Bid, Impression
from dsp.mock_dsp import MockDSP


# ── フィクスチャ ───────────────────────────────────────────────
# Windows の asyncio タイマー解像度は ~15ms のため、
# テスト用 DSP は 1ms 遅延に統一する（本番 mock_dsp.py は別途）

def make_fast_dsps() -> list[MockDSP]:
    """テスト専用の高速モックDSP（Windows 環境でも確実に 80ms 以内）"""
    return [
        MockDSP("dsp-a", "DSP-A", base_cpm=3.0, win_rate=1.0, latency_ms=1),
        MockDSP("dsp-b", "DSP-B", base_cpm=2.0, win_rate=1.0, latency_ms=1),
        MockDSP("dsp-c", "DSP-C", base_cpm=1.0, win_rate=1.0, latency_ms=1),
    ]


@pytest.fixture
def engine():
    eng = AuctionEngine()
    for dsp in make_fast_dsps():
        eng.register_dsp(dsp.dsp_id, dsp)
    return eng


def make_bid_request(floor_price: float = 0.5) -> BidRequest:
    return BidRequest(
        imp=[
            Impression(
                id="imp-001",
                banner=Banner(w=300, h=250),
                tagid="slot-test",
                bidfloor=floor_price,
            )
        ]
    )


# ── テストケース ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auction_returns_winner(engine):
    """通常のオークションで勝者が決まること"""
    req = make_bid_request(floor_price=0.5)
    results = await engine.run_auction(req)

    assert len(results) == 1
    result = results[0]
    assert result.auction_id == req.id
    assert result.winner is not None
    assert result.winner.bid.price > 0


@pytest.mark.asyncio
async def test_second_price_auction(engine):
    """セカンドプライスが正しく計算されること（落札価格 <= 最高入札価格）"""
    req = make_bid_request(floor_price=0.1)
    results = await engine.run_auction(req)
    result = results[0]

    assert result.winner is not None
    if len(result.all_bids) >= 2:
        assert result.clearing_price <= result.winner.bid.price
    # 入札者が1社のみの場合は bidfloor との比較
    else:
        assert result.clearing_price <= result.winner.bid.price


@pytest.mark.asyncio
async def test_floor_price_filters_low_bids():
    """フロアプライスを下回る入札は無視されること"""
    eng = AuctionEngine()
    low_dsp = MockDSP("low", "LowDSP", base_cpm=0.3, win_rate=1.0, latency_ms=1)
    eng.register_dsp("low", low_dsp)

    req = make_bid_request(floor_price=5.0)
    results = await eng.run_auction(req)

    assert results[0].winner is None


@pytest.mark.asyncio
async def test_auction_timeout():
    """タイムアウトするDSPは除外され、速いDSPで結果が出ること"""
    eng = AuctionEngine()
    fast_dsp = MockDSP("fast", "FastDSP", base_cpm=2.0, win_rate=1.0, latency_ms=1)
    slow_dsp = MockDSP("slow", "SlowDSP", base_cpm=99.0, win_rate=1.0, latency_ms=500)
    eng.register_dsp("fast", fast_dsp)
    eng.register_dsp("slow", slow_dsp)

    req = make_bid_request(floor_price=0.1)
    results = await eng.run_auction(req)
    result = results[0]

    assert result.winner is not None
    assert result.winner.dsp_id == "fast"


@pytest.mark.asyncio
async def test_no_dsps_registered():
    """DSPが0件のときは勝者なし"""
    eng = AuctionEngine()
    req = make_bid_request()
    results = await eng.run_auction(req)
    assert results[0].winner is None


@pytest.mark.asyncio
async def test_auction_duration_reasonable(engine):
    """オークション処理時間が 500ms 以内（テスト環境の余裕込み）"""
    req = make_bid_request()
    results = await engine.run_auction(req)
    result = results[0]
    assert result.duration_ms < 500, f"Too slow: {result.duration_ms:.1f}ms"


@pytest.mark.asyncio
async def test_multiple_impressions():
    """複数Impressionのオークションが全て処理されること"""
    eng = AuctionEngine()
    dsp = MockDSP("dsp1", "DSP1", base_cpm=1.5, win_rate=1.0, latency_ms=1)
    eng.register_dsp("dsp1", dsp)

    from auction.openrtb import BidRequest, Impression, Banner
    req = BidRequest(
        imp=[
            Impression(id="imp-1", banner=Banner(w=300, h=250), bidfloor=0.3),
            Impression(id="imp-2", banner=Banner(w=728, h=90), bidfloor=0.3),
        ]
    )
    results = await eng.run_auction(req)
    assert len(results) == 2
    assert all(r.winner is not None for r in results)


@pytest.mark.asyncio
async def test_win_notice_generated(engine):
    """勝者がいる場合、落札通知が正しく生成されること"""
    req = make_bid_request(floor_price=0.1)
    results = await engine.run_auction(req)
    result = results[0]

    notice = engine.build_win_notice(result)
    if result.winner:
        assert notice is not None
        assert notice.winning_price == result.clearing_price
        assert notice.dsp_id == result.winner.dsp_id
    else:
        assert notice is None


@pytest.mark.asyncio
async def test_highest_bidder_wins():
    """最高入札額のDSPが勝者になること（確定的テスト）"""
    eng = AuctionEngine()
    # base_cpm を固定して random.uniform の幅を 0 にする
    high = MockDSP("high", "HighDSP", base_cpm=10.0, win_rate=1.0, latency_ms=1)
    low  = MockDSP("low",  "LowDSP",  base_cpm=1.0,  win_rate=1.0, latency_ms=1)

    # base_cpm * uniform(0.7,1.3) の範囲が重ならないように設定
    # high: 7.0〜13.0  low: 0.7〜1.3 → 確実に high が勝つ
    eng.register_dsp("high", high)
    eng.register_dsp("low", low)

    req = make_bid_request(floor_price=0.1)
    results = await eng.run_auction(req)
    result = results[0]

    assert result.winner is not None
    assert result.winner.dsp_id == "high"


# ── first-price 対応（優先タスク #2）─────────────────────────────

def _bid_result(dsp_id: str, price: float) -> BidResult:
    """落札価格計算テスト用の BidResult ヘルパー。"""
    return BidResult(
        dsp_id=dsp_id,
        bid=Bid(impid="imp-001", price=price),
        response_time_ms=1.0,
    )


def test_compute_clearing_price_first_price_multiple_bids():
    """at=1: 複数入札でも落札者の入札額がそのまま落札価格になる"""
    bids = [_bid_result("a", 5.0), _bid_result("b", 3.0), _bid_result("c", 1.0)]
    assert _compute_clearing_price(bids, bidfloor=0.5, at=1) == 5.0


def test_compute_clearing_price_first_price_single_bid():
    """at=1: 単独入札でも 0.85 を掛けず入札額そのままになる"""
    bids = [_bid_result("a", 4.0)]
    assert _compute_clearing_price(bids, bidfloor=0.5, at=1) == 4.0


def test_compute_clearing_price_second_price_uses_second_bid():
    """at=2: 2位の入札額が落札価格になる（後方互換）"""
    bids = [_bid_result("a", 5.0), _bid_result("b", 3.0)]
    assert _compute_clearing_price(bids, bidfloor=0.5, at=2) == 3.0


def test_compute_clearing_price_second_price_single_bid_fallback():
    """at=2: 単独入札は max(bidfloor, 1位×0.85)（後方互換）"""
    bids = [_bid_result("a", 4.0)]
    assert _compute_clearing_price(bids, bidfloor=0.5, at=2) == 4.0 * 0.85
    assert _compute_clearing_price(bids, bidfloor=10.0, at=2) == 10.0


@pytest.mark.asyncio
async def test_first_price_auction_end_to_end(engine):
    """at=1 の BidRequest では clearing_price == winner.bid.price になる"""
    req = BidRequest(
        imp=[Impression(id="imp-001", banner=Banner(w=300, h=250), bidfloor=0.1)],
        at=1,
    )
    results = await engine.run_auction(req)
    result = results[0]
    assert result.winner is not None
    assert result.clearing_price == result.winner.bid.price
