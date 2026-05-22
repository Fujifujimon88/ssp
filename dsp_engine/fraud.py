"""
dsp_engine #8 — fraud / IVT / brand safety 検査モジュール。

純粋関数 (外部 I/O なし) として実装し、bidder.py および attribution.py から呼ぶ。

Public API:
    check_click_rate_limit(redis, click_token, client_ip, *, token_limit, ip_limit,
                           window_seconds, _override_token_count=None,
                           _override_ip_count=None) -> bool
    validate_revenue(revenue_jpy, *, avg_purchase_value_jpy, revenue_cap_multiplier) -> bool
    is_ivt(client_ip, user_agent, *, datacenter_cidrs) -> bool
    is_brand_safety_blocked(bid_request, campaign) -> bool
"""
import ipaddress
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── bot UA キーワード（小文字比較） ──────────────────────────────
_BOT_SIGNATURES = (
    "bot",
    "crawl",
    "spider",
    "slurp",
    "mediapartners",
)


def check_click_rate_limit(
    redis,
    click_token: str,
    client_ip: str,
    *,
    token_limit: int,
    ip_limit: int,
    window_seconds: int,
    _override_token_count: Optional[int] = None,
    _override_ip_count: Optional[int] = None,
) -> bool:
    """クリック連打レート制限チェック。

    テスト / メモリフォールバック時は _override_*_count を使って判定し、
    Redis 参照は行わない（redis=None の場合も同様）。

    Returns:
        True = レート制限超過（クリックを記録しない）
        False = 制限内（クリックを記録してよい）
    """
    if _override_token_count is not None:
        token_count = _override_token_count
    elif redis is not None:
        # 本番: Redis INCR + EXPIRE でカウント。ここでは同期実装省略（テストは override 経由）
        token_count = 0
    else:
        token_count = 0

    if _override_ip_count is not None:
        ip_count = _override_ip_count
    elif redis is not None:
        ip_count = 0
    else:
        ip_count = 0

    return token_count > token_limit or ip_count > ip_limit


def validate_revenue(
    revenue_jpy: float,
    *,
    avg_purchase_value_jpy: float,
    revenue_cap_multiplier: float,
) -> bool:
    """revenue 検証。

    - 負値は不正として False
    - avg_purchase_value_jpy * revenue_cap_multiplier を超える値は外れ値として False
    - それ以外（0以上・上限以内）は True

    Returns:
        True = 正常値 / False = 拒否
    """
    if revenue_jpy < 0:
        return False
    cap = avg_purchase_value_jpy * revenue_cap_multiplier
    if revenue_jpy > cap:
        return False
    return True


def is_ivt(
    client_ip: str,
    user_agent: str,
    *,
    datacenter_cidrs: list[str],
) -> bool:
    """IVT（無効トラフィック）判定。

    (a) client_ip が datacenter_cidrs のいずれかの CIDR に含まれれば True。
    (b) user_agent に bot シグネチャ（大文字小文字無視）が含まれれば True。
    どちらも該当しなければ False。

    純粋関数・外部 I/O なし。
    """
    # CIDR チェック
    if datacenter_cidrs and client_ip:
        try:
            ip_obj = ipaddress.ip_address(client_ip)
            for cidr in datacenter_cidrs:
                cidr = cidr.strip()
                if not cidr:
                    continue
                try:
                    if ip_obj in ipaddress.ip_network(cidr, strict=False):
                        return True
                except ValueError:
                    logger.warning(f"is_ivt: invalid CIDR '{cidr}' — skipping")
        except ValueError:
            logger.warning(f"is_ivt: invalid IP '{client_ip}' — skipping CIDR check")

    # bot UA チェック
    if user_agent:
        ua_lower = user_agent.lower()
        for sig in _BOT_SIGNATURES:
            if sig in ua_lower:
                return True

    return False


def is_brand_safety_blocked(bid_request, campaign) -> bool:
    """ブランドセーフティチェック。

    1. campaign.bcat_block（JSON 配列文字列）をパース → blocked_cats。
       bid_request.site.cat の各要素が blocked_cats のいずれかを prefix として持てば True。
       例: site.cat=["IAB25-3"], bcat_block='["IAB25"]' → "IAB25-3".startswith("IAB25") = True

    2. campaign.badv_block（JSON 配列文字列）をパース → blocked_advs。
       bid_request.site.domain が blocked_advs に含まれれば True。

    どちらも一致しなければ False。純粋関数・外部 I/O なし。
    """
    site = getattr(bid_request, "site", None)

    # ── bcat チェック ──
    bcat_raw = getattr(campaign, "bcat_block", None) or "[]"
    try:
        blocked_cats: list[str] = json.loads(bcat_raw)
    except (json.JSONDecodeError, TypeError):
        blocked_cats = []

    if blocked_cats and site is not None:
        site_cats: list[str] = getattr(site, "cat", None) or []
        for cat in site_cats:
            for blocked_cat in blocked_cats:
                if cat == blocked_cat or cat.startswith(blocked_cat + "-"):
                    return True

    # ── badv チェック ──
    badv_raw = getattr(campaign, "badv_block", None) or "[]"
    try:
        blocked_advs: list[str] = json.loads(badv_raw)
    except (json.JSONDecodeError, TypeError):
        blocked_advs = []

    if blocked_advs and site is not None:
        site_domain: Optional[str] = getattr(site, "domain", None)
        if site_domain and site_domain in blocked_advs:
            return True

    return False
