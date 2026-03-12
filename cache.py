"""
Redis キャッシュ・落札トークン管理
Redis が起動していない場合はインメモリフォールバックを自動使用
"""
import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

WIN_TOKEN_TTL = 60
REPORT_CACHE_TTL = 300

# インメモリフォールバック（Redis未起動時）
_memory_store: dict[str, tuple[str, float | None]] = {}  # {key: (value, expires_monotonic)}


def _mem_set(key: str, value: str, ttl: int | None = None) -> None:
    import time
    exp = time.monotonic() + ttl if ttl is not None else None
    _memory_store[key] = (value, exp)


def _mem_get(key: str) -> str | None:
    import time
    entry = _memory_store.get(key)
    if entry is None:
        return None
    val, exp = entry
    if exp is not None and time.monotonic() > exp:
        _mem_delete(key)
        return None
    return val


def _mem_delete(key: str) -> None:
    _memory_store.pop(key, None)


def is_redis_connected() -> bool:
    return not _use_memory and _redis is not None
_use_memory = False
_redis = None


async def get_redis():
    global _redis, _use_memory
    if _use_memory:
        return None
    if _redis is None:
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
            await r.ping()
            _redis = r
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), using in-memory fallback")
            _use_memory = True
            return None
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


# ── 落札トークン ────────────────────────────────────────────────

async def set_win_token(token: str, data: dict) -> None:
    r = await get_redis()
    if r:
        await r.setex(f"win:{token}", WIN_TOKEN_TTL, json.dumps(data))
    else:
        _mem_set(f"win:{token}", json.dumps(data), WIN_TOKEN_TTL)


async def get_win_token(token: str) -> Optional[dict]:
    r = await get_redis()
    key = f"win:{token}"
    if r:
        val = await r.get(key)
    else:
        val = _mem_get(key)
    return json.loads(val) if val else None


async def delete_win_token(token: str) -> None:
    r = await get_redis()
    key = f"win:{token}"
    if r:
        await r.delete(key)
    else:
        _mem_delete(key)


# ── レポートキャッシュ ──────────────────────────────────────────

async def cache_report(key: str, data: dict) -> None:
    r = await get_redis()
    if r:
        await r.setex(f"report:{key}", REPORT_CACHE_TTL, json.dumps(data))
    else:
        _mem_set(f"report:{key}", json.dumps(data), REPORT_CACHE_TTL)


async def get_cached_report(key: str) -> Optional[dict]:
    r = await get_redis()
    cache_key = f"report:{key}"
    if r:
        val = await r.get(cache_key)
    else:
        val = _mem_get(cache_key)
    return json.loads(val) if val else None


# ── インプレッションカウンター ──────────────────────────────────

async def incr_impression_counter(publisher_id: str, date_str: str) -> int:
    r = await get_redis()
    key = f"imp:{publisher_id}:{date_str}"
    if r:
        count = await r.incr(key)
        await r.expire(key, 86400 * 2)
        return count
    else:
        current = int(_mem_get(key) or "0")
        _mem_set(key, str(current + 1), 86400 * 2)
        return current + 1
