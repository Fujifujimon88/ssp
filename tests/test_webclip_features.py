"""
iOS MDM WebClip / Safari 機能テスト

テスト対象:
  - _webclip_payload(): アイコンなし / アイコンあり
  - SafariConfig + _safari_payload()
  - generate_mobileconfig(): safari パラメータ対応
  - add_web_clip() コマンド: アイコンなし / アイコンあり
"""
import base64
import plistlib
from unittest.mock import MagicMock, patch

import pytest

from mdm.enrollment.mobileconfig import (
    SafariConfig,
    WebClipConfig,
    _safari_payload,
    _webclip_payload,
    generate_mobileconfig,
)
from mdm.nanomdm.commands import add_web_clip


class TestWebClipPayload:
    def test_basic_webclip_no_icon(self):
        """アイコンなしのWebClipペイロードが正しく生成される"""
        clip = WebClipConfig(url="https://example.com", label="テスト")
        payload = _webclip_payload(clip)
        assert payload["PayloadType"] == "com.apple.webClip.managed"
        assert payload["URL"] == "https://example.com"
        assert payload["Label"] == "テスト"
        assert payload["FullScreen"] is True
        assert "Icon" not in payload
        assert "PrecomposedIcon" not in payload

    def test_webclip_with_icon_url(self):
        """アイコンURLがある場合、Base64エンコードされたIconが含まれる"""
        fake_img = b"\x89PNG\r\n\x1a\n"  # PNGマジックバイト（ダミー）
        mock_response = MagicMock()
        mock_response.content = fake_img

        with patch("mdm.enrollment.mobileconfig.httpx.get", return_value=mock_response):
            clip = WebClipConfig(url="https://example.com", label="テスト", icon_url="https://example.com/icon.png")
            payload = _webclip_payload(clip)

        assert "Icon" in payload
        assert payload["Icon"] == base64.b64encode(fake_img).decode()
        assert payload["PrecomposedIcon"] is True

    def test_webclip_icon_fetch_failure_is_ignored(self):
        """アイコン取得失敗時はアイコンなしで続行する"""
        with patch("mdm.enrollment.mobileconfig.httpx.get", side_effect=Exception("network error")):
            clip = WebClipConfig(url="https://example.com", label="テスト", icon_url="https://bad.example.com/icon.png")
            payload = _webclip_payload(clip)

        assert payload["URL"] == "https://example.com"
        assert "Icon" not in payload


class TestSafariConfig:
    def test_safari_payload_with_homepage(self):
        """ホームページURLが設定される"""
        safari = SafariConfig(home_page="https://example.com", default_search_provider="Bing")
        payload = _safari_payload(safari)
        assert payload["PayloadType"] == "com.apple.safari"
        assert payload["HomePage"] == "https://example.com"
        assert payload["DefaultSearchProvider"] == "Bing"

    def test_safari_payload_default_search_provider(self):
        """デフォルトはGoogle"""
        safari = SafariConfig()
        payload = _safari_payload(safari)
        assert payload["DefaultSearchProvider"] == "Google"
        assert "HomePage" not in payload

    def test_safari_payload_no_homepage_when_none(self):
        """home_pageがNoneの場合はHomePageキーが含まれない"""
        safari = SafariConfig(home_page=None)
        payload = _safari_payload(safari)
        assert "HomePage" not in payload


class TestGenerateMobileconfig:
    def test_generate_without_safari(self):
        """safari=Noneの場合、Safariペイロードが含まれない"""
        result = generate_mobileconfig(profile_name="テスト")
        profile = plistlib.loads(result)
        payload_types = [p["PayloadType"] for p in profile["PayloadContent"]]
        assert "com.apple.safari" not in payload_types

    def test_generate_with_safari(self):
        """safari指定時にSafariペイロードが含まれる"""
        safari = SafariConfig(home_page="https://example.com")
        result = generate_mobileconfig(profile_name="テスト", safari=safari)
        profile = plistlib.loads(result)
        payload_types = [p["PayloadType"] for p in profile["PayloadContent"]]
        assert "com.apple.safari" in payload_types

    def test_generate_with_webclip_and_safari(self):
        """WebClipとSafariを同時に含むプロファイルが生成される"""
        clips = [WebClipConfig(url="https://example.com", label="テスト")]
        safari = SafariConfig(home_page="https://example.com")
        result = generate_mobileconfig(webclips=clips, safari=safari)
        profile = plistlib.loads(result)
        payload_types = [p["PayloadType"] for p in profile["PayloadContent"]]
        assert "com.apple.webClip.managed" in payload_types
        assert "com.apple.safari" in payload_types

    def test_generate_result_is_valid_plist(self):
        """生成結果が有効なplist XMLである"""
        result = generate_mobileconfig()
        assert result.startswith(b"<?xml")
        profile = plistlib.loads(result)
        assert profile["PayloadType"] == "Configuration"


class TestAddWebClipCommand:
    def test_add_web_clip_basic(self):
        """基本的なWebClipコマンドが正しく生成される"""
        plist_bytes = add_web_clip(url="https://example.com", label="テスト")
        cmd = plistlib.loads(plist_bytes)
        assert cmd["Command"]["RequestType"] == "InstallProfile"
        profile = cmd["Command"]["Payload"]
        webclip = profile["PayloadContent"][0]
        assert webclip["URL"] == "https://example.com"
        assert webclip["Label"] == "テスト"
        assert "Icon" not in webclip

    def test_add_web_clip_with_icon(self):
        """アイコンURL付きのWebClipコマンドが正しく生成される"""
        fake_img = b"\x89PNG\r\n\x1a\n"
        mock_response = MagicMock()
        mock_response.content = fake_img

        with patch("mdm.nanomdm.commands.httpx.get", return_value=mock_response):
            plist_bytes = add_web_clip(
                url="https://example.com",
                label="テスト",
                icon_url="https://example.com/icon.png",
            )

        cmd = plistlib.loads(plist_bytes)
        webclip = cmd["Command"]["Payload"]["PayloadContent"][0]
        assert "Icon" in webclip
        assert webclip["PrecomposedIcon"] is True
