"""
dsp_engine 通貨換算レート（円 / ドル）。

Phase 1 では bidder.py に固定値 150.0 をハードコードしていた。Phase 2 で
設定（settings.jpy_per_usd）駆動にし、実行中の動的更新フックも用意する。

実 FX API 連携（為替レートの自動取得）は今後の課題。set_jpy_per_usd() を
スケジュールタスクから呼べば日次更新などに対応できる。
"""
import logging

from config import settings

logger = logging.getLogger(__name__)

# 実行中の上書き値（None のときは settings.jpy_per_usd を使う）
_override: float | None = None


def get_jpy_per_usd() -> float:
    """現在の円/ドルレートを返す。"""
    if _override is not None:
        return _override
    return settings.jpy_per_usd


def set_jpy_per_usd(rate: float) -> None:
    """円/ドルレートを動的更新する（正の値のみ受け付ける）。"""
    global _override
    if rate and rate > 0:
        _override = float(rate)
        logger.info(f"dsp-engine: JPY/USD rate updated -> {_override}")


def usd_to_jpy(usd: float) -> float:
    return usd * get_jpy_per_usd()


def jpy_to_usd(jpy: float) -> float:
    rate = get_jpy_per_usd()
    return jpy / rate if rate > 0 else 0.0
