"""
dsp_engine ads.txt / app-ads.txt 検証。

IAB Tech Lab ads.txt / app-ads.txt 仕様に基づき、パブリッシャーサイト・アプリの
ads.txt をパース・検証する。HTTP fetch（fetch_ads_txt）は入札パス外専用
（batch.py のバックグラウンドループ）から呼ぶ。parse / verify は純粋関数。

仕様: https://iabtechlab.com/ads-txt/
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SEC = 5.0


@dataclass
class AdsTxtEntry:
    domain: str            # 広告システムのドメイン（例: "ssp-platform.example.com"）
    account_id: str        # 当該広告システムでのアカウント ID
    account_type: str      # "DIRECT" / "RESELLER"
    cert_authority: Optional[str] = None  # 認証局 ID（任意の4要素目）


def parse_ads_txt(content: str) -> list[AdsTxtEntry]:
    """ads.txt / app-ads.txt のテキストをパースして AdsTxtEntry のリストを返す。

    コメント行（#）・空行をスキップし、カンマ区切り3要素未満の不正行は無視する。
    """
    entries: list[AdsTxtEntry] = []
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
            logger.debug(f"adstxt: skip malformed line: {raw!r}")
            continue
        entries.append(AdsTxtEntry(
            domain=parts[0].lower(),
            account_id=parts[1],
            account_type=parts[2].upper(),
            cert_authority=parts[3] if len(parts) > 3 and parts[3] else None,
        ))
    return entries


async def fetch_ads_txt(domain: str, *, app: bool = False) -> Optional[list[AdsTxtEntry]]:
    """パブリッシャーサイト / アプリの ads.txt（app-ads.txt）を取得してパースする。

    app=False → https://{domain}/ads.txt
    app=True  → https://{domain}/app-ads.txt
    未設置（404）や fetch 失敗は None を返す（入札パス外専用）。
    """
    path = "app-ads.txt" if app else "ads.txt"
    url = f"https://{domain}/{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(FETCH_TIMEOUT_SEC)) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return parse_ads_txt(resp.text)
    except Exception as exc:
        logger.warning(f"ads.txt fetch failed for {url}: {exc}")
        return None


def verify_publisher_in_ads_txt(
    entries: Optional[list[AdsTxtEntry]],
    ssp_domain: str,
    publisher_id: str,
) -> bool:
    """ads.txt に「自社 SSP ドメイン + publisher_id の DIRECT 行」があるか確認する。

    entries=None（fetch 失敗・未設置）はフォールバックで True（突合スキップ）。
    """
    if entries is None:
        return True
    ssp = ssp_domain.lower()
    for e in entries:
        if e.domain == ssp and e.account_id == publisher_id and e.account_type == "DIRECT":
            return True
    return False
