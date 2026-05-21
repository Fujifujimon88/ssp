"""
dsp_engine 予算ペーシング。

日予算を24時間で線形に均す smooth pacing。現在時刻までに消化してよい累計額を
超えていれば入札を止める。並行リクエストでのオーバーラン対策として
安全率 SAFETY_MARGIN（90%）でバッファを取る。

消化額の保存先:
  - Redis 利用可能時: INCRBYFLOAT で原子的に加算（cache.get_redis を再利用）
  - Redis 不在時: プロセス内 dict にフォールバック
"""
import logging
from datetime import date as date_type, datetime

from cache import get_redis
from utils import utcnow

logger = logging.getLogger(__name__)

SAFETY_MARGIN = 0.9          # ペース許容額の90%で入札停止
_KEY_PREFIX = "dsp:pace"
_SPEND_TTL_SEC = 86400 * 2   # 消化カウンタは2日で失効

# Redis 不在時のインメモリフォールバック {key: spend_jpy}
_mem_spend: dict[str, float] = {}


def paced_budget_allowed(daily_budget_jpy: float, now: datetime) -> float:
    """now 時点で消化してよい累計額（円）を返す。日予算0以下なら inf（無制限）。"""
    if daily_budget_jpy <= 0:
        return float("inf")
    hourly = daily_budget_jpy / 24.0
    elapsed_hours = now.hour + now.minute / 60.0 + now.second / 3600.0
    return hourly * elapsed_hours


class BudgetPacer:
    """キャンペーン日予算の消化を追跡し、ペース超過時に入札を止める。"""

    def _key(self, campaign_id: str, day: date_type) -> str:
        return f"{_KEY_PREFIX}:{campaign_id}:{day.isoformat()}"

    async def get_spend(self, campaign_id: str, day: date_type | None = None) -> float:
        """指定日（既定: 当日UTC）のキャンペーン累計消化額（円）。"""
        day = day or utcnow().date()
        key = self._key(campaign_id, day)
        r = await get_redis()
        if r:
            val = await r.get(key)
            return float(val) if val else 0.0
        return _mem_spend.get(key, 0.0)

    async def record_spend(self, campaign_id: str, amount_jpy: float,
                           now: datetime | None = None) -> float:
        """消化額を加算し、加算後の累計額を返す。"""
        now = now or utcnow()
        key = self._key(campaign_id, now.date())
        r = await get_redis()
        if r:
            new_total = await r.incrbyfloat(key, amount_jpy)
            await r.expire(key, _SPEND_TTL_SEC)
            return float(new_total)
        _mem_spend[key] = _mem_spend.get(key, 0.0) + amount_jpy
        return _mem_spend[key]

    async def can_bid(self, campaign, now: datetime | None = None) -> bool:
        """キャンペーンが現時点で入札可能か（ペース内か）を返す。"""
        now = now or utcnow()
        if campaign.daily_budget_jpy <= 0:
            return True  # 無制限
        allowed = paced_budget_allowed(campaign.daily_budget_jpy, now) * SAFETY_MARGIN
        spent = await self.get_spend(campaign.id, now.date())
        return spent < allowed
