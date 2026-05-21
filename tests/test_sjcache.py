"""
dsp_engine sellers.json キャッシュ・突合のユニットテスト（優先タスク #3 Phase B）

入札パス内で使う関数（lookup_seller / get_cached_sellers）は外部 I/O なし。
fetch_sellers_json は入札パス外専用（httpx をモックして検証）。

実行: cd ssp_platform && pytest tests/test_sjcache.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dsp_engine.sjcache import (
    fetch_sellers_json,
    get_cached_sellers,
    lookup_seller,
    prime_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """L1 メモリキャッシュをテスト間で隔離する"""
    from dsp_engine import sjcache
    sjcache._memory_cache.clear()
    yield
    sjcache._memory_cache.clear()


_SAMPLE = {
    "sellers": [
        {"seller_id": "pub-1", "domain": "publisher.com", "seller_type": "PUBLISHER"},
        {"seller_id": "exch-9", "domain": "exchange1.com", "seller_type": "INTERMEDIARY"},
    ]
}


# ── lookup_seller（純粋関数）─────────────────────────────────────

def test_lookup_seller_found():
    """sid + asi(domain) が一致する seller があれば True"""
    assert lookup_seller(_SAMPLE, sid="pub-1", asi="publisher.com") is True
    assert lookup_seller(_SAMPLE, sid="exch-9", asi="Exchange1.com") is True  # 大小無視


def test_lookup_seller_not_found():
    """一致する seller がなければ False"""
    assert lookup_seller(_SAMPLE, sid="pub-1", asi="other.com") is False
    assert lookup_seller(_SAMPLE, sid="unknown", asi="publisher.com") is False


def test_lookup_seller_none_cache():
    """sellers_json=None はフォールバックで True（突合スキップ）"""
    assert lookup_seller(None, sid="pub-1", asi="publisher.com") is True


# ── get_cached_sellers（L1 キャッシュ参照）──────────────────────

def test_get_cached_sellers_l1_hit():
    """prime_cache 済みなら L1 から取得できる"""
    prime_cache("exch-a", _SAMPLE)
    assert get_cached_sellers("exch-a") == _SAMPLE


def test_get_cached_sellers_from_raw():
    """L1 miss でも raw_cache（JSON文字列）を渡せばパースして返す"""
    import json
    raw = json.dumps(_SAMPLE)
    assert get_cached_sellers("exch-b", raw_cache=raw) == _SAMPLE
    # 2回目は L1 ヒットする
    assert get_cached_sellers("exch-b") == _SAMPLE


def test_get_cached_sellers_miss():
    """L1 なし・raw_cache なし → None"""
    assert get_cached_sellers("exch-none") is None


def test_get_cached_sellers_invalid_raw():
    """raw_cache が不正 JSON なら None（例外を出さない）"""
    assert get_cached_sellers("exch-bad", raw_cache="{not json") is None


# ── fetch_sellers_json（入札パス外・httpx モック）───────────────

def _mock_httpx_client(*, json_payload=None, side_effect=None):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=json_payload)
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
async def test_fetch_sellers_json_success():
    """正常 fetch で sellers.json dict が返る"""
    with patch("httpx.AsyncClient", return_value=_mock_httpx_client(json_payload=_SAMPLE)):
        result = await fetch_sellers_json("https://exchange1.com/sellers.json")
    assert result == _SAMPLE


@pytest.mark.asyncio
async def test_fetch_sellers_json_failure_returns_none():
    """fetch 失敗（タイムアウト等）は None を返しプロセスを止めない"""
    with patch("httpx.AsyncClient",
               return_value=_mock_httpx_client(side_effect=httpx.TimeoutException("timeout"))):
        result = await fetch_sellers_json("https://slow.example.com/sellers.json")
    assert result is None
