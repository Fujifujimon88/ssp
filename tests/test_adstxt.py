"""
dsp_engine ads.txt / app-ads.txt 検証のユニットテスト（優先タスク #3 Phase C）

parse_ads_txt / verify_publisher_in_ads_txt は純粋関数。
fetch_ads_txt は入札パス外専用（httpx をモックして検証）。

実行: cd ssp_platform && pytest tests/test_adstxt.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dsp_engine.adstxt import (
    AdsTxtEntry,
    fetch_ads_txt,
    parse_ads_txt,
    verify_publisher_in_ads_txt,
)


# ── parse_ads_txt（純粋関数）─────────────────────────────────────

def test_parse_ads_txt_direct_line():
    """DIRECT 行を domain/account_id/account_type にパースする"""
    entries = parse_ads_txt("ssp-platform.example.com, pub-1, DIRECT")
    assert len(entries) == 1
    assert entries[0].domain == "ssp-platform.example.com"
    assert entries[0].account_id == "pub-1"
    assert entries[0].account_type == "DIRECT"


def test_parse_ads_txt_skip_comment_and_blank():
    """コメント行（#）と空行はスキップする"""
    content = "# comment\n\nssp.example.com, p1, DIRECT\n   \n# tail"
    entries = parse_ads_txt(content)
    assert len(entries) == 1


def test_parse_ads_txt_skip_malformed():
    """カンマ区切り3要素未満の不正行は無視する"""
    content = "incomplete, line\nssp.example.com, p1, DIRECT"
    entries = parse_ads_txt(content)
    assert len(entries) == 1
    assert entries[0].account_id == "p1"


def test_parse_ads_txt_with_cert_authority():
    """4要素目の cert_authority もパースする"""
    entries = parse_ads_txt("ssp.example.com, p1, RESELLER, f08c47fec0942fa0")
    assert entries[0].cert_authority == "f08c47fec0942fa0"
    assert entries[0].account_type == "RESELLER"


# ── verify_publisher_in_ads_txt（純粋関数）──────────────────────

def test_verify_publisher_in_ads_txt_found():
    """自社ドメイン + publisher_id の DIRECT 行があれば True"""
    entries = parse_ads_txt("ssp-platform.example.com, pub-1, DIRECT")
    assert verify_publisher_in_ads_txt(entries, "ssp-platform.example.com", "pub-1") is True


def test_verify_publisher_in_ads_txt_missing():
    """一致行がなければ False"""
    entries = parse_ads_txt("other.com, pub-9, DIRECT")
    assert verify_publisher_in_ads_txt(entries, "ssp-platform.example.com", "pub-1") is False


def test_verify_publisher_none_entries():
    """entries=None（fetch 失敗）はフォールバックで True"""
    assert verify_publisher_in_ads_txt(None, "ssp-platform.example.com", "pub-1") is True


# ── fetch_ads_txt（入札パス外・httpx モック）─────────────────────

def _mock_client(*, status: int, text: str):
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.text = text
    mock_resp.raise_for_status = MagicMock()
    mc = MagicMock()
    mc.get = AsyncMock(return_value=mock_resp)
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=None)
    return mc


@pytest.mark.asyncio
async def test_fetch_ads_txt_success():
    """正常 fetch で AdsTxtEntry のリストが返る"""
    mc = _mock_client(status=200, text="ssp.example.com, p1, DIRECT")
    with patch("httpx.AsyncClient", return_value=mc):
        entries = await fetch_ads_txt("publisher.com")
    assert entries is not None and len(entries) == 1
    assert isinstance(entries[0], AdsTxtEntry)


@pytest.mark.asyncio
async def test_fetch_ads_txt_404_returns_none():
    """ads.txt 未設置（404）は None を返す"""
    mc = _mock_client(status=404, text="")
    with patch("httpx.AsyncClient", return_value=mc):
        entries = await fetch_ads_txt("publisher.com")
    assert entries is None


@pytest.mark.asyncio
async def test_fetch_app_ads_txt_uses_correct_path():
    """app=True のとき /app-ads.txt を取得する"""
    mc = _mock_client(status=200, text="ssp.example.com, p1, DIRECT")
    with patch("httpx.AsyncClient", return_value=mc):
        await fetch_ads_txt("apps.example.com", app=True)
    called_url = mc.get.call_args[0][0]
    assert called_url.endswith("/app-ads.txt")
