"""
dsp_engine sellers.json キャッシュ・突合。

外部エクスチェンジの sellers.json を突合して schain ノードの正当性を確認する。
HTTP fetch（fetch_sellers_json）は入札パス外（batch.py のバックグラウンドループ）
からのみ呼ぶ。入札パス内で許されるのは L1 メモリキャッシュ参照（get_cached_sellers）
と純粋関数の突合（lookup_seller）のみ。
"""
import json
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SEC = 5.0
_MEMORY_TTL_SEC = 300  # L1 メモリキャッシュの TTL（秒）

# L1 キャッシュ: exchange_name -> (monotonic_ts, parsed sellers.json dict)
_memory_cache: dict[str, tuple[float, dict]] = {}


async def fetch_sellers_json(url: str) -> Optional[dict]:
    """外部エクスチェンジの sellers.json を HTTP GET する（入札パス外専用）。

    失敗時は None を返し、呼び出し側（batch）を止めない。
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(FETCH_TIMEOUT_SEC)) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning(f"sellers.json fetch failed for {url}: {exc}")
        return None


def prime_cache(exchange_name: str, sellers_json: dict) -> None:
    """L1 メモリキャッシュへ載せる（batch / 入札パスのキャッシュウォーミング用）。"""
    _memory_cache[exchange_name] = (time.monotonic(), sellers_json)


def get_cached_sellers(
    exchange_name: str, raw_cache: Optional[str] = None
) -> Optional[dict]:
    """入札パス内から呼ぶ sellers.json 参照（同期・外部 I/O なし）。

    L1 メモリキャッシュを優先。miss かつ raw_cache（DspConfigDB.sellers_json_cache
    の JSON 文字列）が与えられればパースして L1 に載せる。どちらも無ければ None。
    """
    hit = _memory_cache.get(exchange_name)
    if hit is not None and time.monotonic() - hit[0] < _MEMORY_TTL_SEC:
        return hit[1]
    if raw_cache:
        try:
            data = json.loads(raw_cache)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(data, dict):
            prime_cache(exchange_name, data)
            return data
    return None


def lookup_seller(sellers_json: Optional[dict], sid: str, asi: str) -> bool:
    """sellers.json 内に seller_id=sid かつ domain=asi の seller があるか（純粋関数）。

    sellers_json=None はフォールバックで True（突合スキップ。RTB 業界慣行）。
    """
    if sellers_json is None:
        return True
    for s in sellers_json.get("sellers", []):
        if (str(s.get("seller_id", "")) == sid
                and str(s.get("domain", "")).lower() == asi.lower()):
            return True
    return False
