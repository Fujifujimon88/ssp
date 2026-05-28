"""動的フロア最適化 — 純粋関数 (dsp_engine #11 Phase 2)。

publisher 別の最適フロア CPM(USD) を算出する純粋関数を提供する。
DB I/O・async・L1 cache は本モジュールに含まない (Phase 3 の責務)。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class FloorConfig:
    FLOOR_LOOKBACK_DAYS: int = 7
    FLOOR_COLD_START_MIN: int = 10
    FLOOR_PERCENTILE: int = 50
    TARGET_WIN_RATE: float = 0.3
    WIN_RATE_SENSITIVITY: float = 0.5
    DENSITY_SENSITIVITY: float = 0.1
    FLOOR_REFRESH_SEC: int = 3600


DEFAULT_FLOOR_CONFIG = FloorConfig()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_dynamic_floor(
    cleared_prices_jpy: list[float],
    win_rate: float,
    bid_density: float,
    jpy_per_usd: float,
    config: FloorConfig | None = None,
) -> float | None:
    cfg = config if config is not None else DEFAULT_FLOOR_CONFIG
    if len(cleared_prices_jpy) < cfg.FLOOR_COLD_START_MIN:
        return None
    price_anchor_jpy = statistics.median(cleared_prices_jpy)
    win_rate_factor = _clamp(
        1.0 + (win_rate - cfg.TARGET_WIN_RATE) * cfg.WIN_RATE_SENSITIVITY,
        0.5,
        2.0,
    )
    density_factor = _clamp(
        1.0 + max(0.0, bid_density - 1.0) * cfg.DENSITY_SENSITIVITY,
        1.0,
        1.5,
    )
    floor_jpy = price_anchor_jpy * win_rate_factor * density_factor
    return floor_jpy / jpy_per_usd


def _extract_publisher_id(bid_request) -> str | None:
    """OpenRTB BidRequest から publisher_id を解決する純粋関数。

    優先順: site.publisher.id → app.publisher.id → None。
    site / app / publisher のいずれかが None でも AttributeError を出さない。
    """
    site = getattr(bid_request, "site", None)
    if site is not None:
        publisher = getattr(site, "publisher", None)
        if publisher is not None:
            pub_id = getattr(publisher, "id", None)
            if pub_id is not None:
                return pub_id
    app = getattr(bid_request, "app", None)
    if app is not None:
        publisher = getattr(app, "publisher", None)
        if publisher is not None:
            pub_id = getattr(publisher, "id", None)
            if pub_id is not None:
                return pub_id
    return None
