"""
dsp_engine 外部エクスチェンジ連携（受信側 / Phase 2）。

外部 SSP・エクスチェンジは POST /dsp-engine/exchange/{name}/bid でこちらへ
OpenRTB 入札リクエストを送る。本モジュールはエクスチェンジの識別（登録済み・
有効か）、QPS 制御、入札/落札の簡易統計を担う。

エクスチェンジは SSP 連携画面（DspConfigDB / dsp_configs）で登録・有効化する。
"""
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspConfigDB

logger = logging.getLogger(__name__)

# ── QPS 制御（固定1秒ウィンドウ・プロセス内） ──────────────────
_qps_window: dict[str, tuple[int, int]] = {}  # {exchange: (window_epoch_sec, count)}


def check_qps(exchange_name: str, qps_limit: int) -> bool:
    """現在の1秒で受信数が qps_limit 以内なら True。qps_limit<=0 は無制限。"""
    if qps_limit <= 0:
        return True
    now = int(time.time())
    win, count = _qps_window.get(exchange_name, (now, 0))
    if win != now:
        win, count = now, 0
    count += 1
    _qps_window[exchange_name] = (win, count)
    return count <= qps_limit


async def get_active_exchange(db: AsyncSession, name: str) -> DspConfigDB | None:
    """active=True の登録済みエクスチェンジを返す（未登録/停止中は None）。"""
    return await db.scalar(
        select(DspConfigDB).where(
            DspConfigDB.name == name, DspConfigDB.active.is_(True)
        )
    )


def verify_exchange_secret(exchange: DspConfigDB, provided_secret: str | None) -> bool:
    """エクスチェンジ認証。

    api_secret 未設定（NULL/空）のエクスチェンジは認証不要で常に True。
    設定済みの場合は X-DSP-Secret ヘッダー値との完全一致を要求する。
    """
    required = getattr(exchange, "api_secret", None)
    if not required:
        return True
    return provided_secret == required


# ── 入札/落札の簡易統計（プロセス内・SSP連携画面表示用） ────────
_stats: dict[str, dict] = {}  # {exchange: {"bids","wins","latency_sum"}}


def _bucket(exchange_name: str) -> dict:
    return _stats.setdefault(
        exchange_name, {"bids": 0, "wins": 0, "latency_sum": 0.0}
    )


def record_bid_stat(exchange_name: str, latency_ms: float) -> None:
    s = _bucket(exchange_name)
    s["bids"] += 1
    s["latency_sum"] += latency_ms


def record_win_stat(exchange_name: str) -> None:
    _bucket(exchange_name)["wins"] += 1


def get_stats(exchange_name: str) -> dict:
    """{"bids","wins","win_rate","avg_latency_ms"} を返す。"""
    s = _stats.get(exchange_name, {"bids": 0, "wins": 0, "latency_sum": 0.0})
    bids = s["bids"]
    return {
        "bids": bids,
        "wins": s["wins"],
        "win_rate": (s["wins"] / bids) if bids > 0 else None,
        "avg_latency_ms": (s["latency_sum"] / bids) if bids > 0 else None,
    }


async def persist_exchange_stats(db: AsyncSession, exchange_name: str) -> None:
    """プロセス内統計を DspConfigDB に書き戻す（落札通知時など低頻度に呼ぶ）。"""
    conn = await db.scalar(
        select(DspConfigDB).where(DspConfigDB.name == exchange_name)
    )
    if conn is None:
        return
    stats = get_stats(exchange_name)
    conn.last_win_rate = stats["win_rate"]
    conn.last_latency_ms = stats["avg_latency_ms"]
    await db.commit()
