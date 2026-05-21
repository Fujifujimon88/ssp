"""
OpenRTB 2.6 スキーマ拡張テスト（dsp_engine 優先タスク #1）
後方互換 + 新フィールド（app / schain / GPP / eids / burl・lurl / PMP・Deal /
Video 詳細 / Device 拡張）のパース・保持を検証する。
実行: cd ssp_platform && pytest tests/test_openrtb_26.py -v
"""
import pytest

from auction.openrtb import (
    App,
    Banner,
    Bid,
    BidRequest,
    Deal,
    Device,
    ExtendedId,
    Geo,
    Impression,
    Pmp,
    Regs,
    Source,
    SourceExt,
    SupplyChain,
    SupplyChainNode,
    User,
    UserExt,
    UserIdEntry,
    Video,
)


# ── 後方互換テスト ──────────────────────────────────────────────

def test_backward_compat_minimal_bid_request():
    """2.5 相当の最小 BidRequest が引き続き生成でき、新フィールドは None"""
    req = BidRequest(imp=[Impression(id="imp-1", banner=Banner(w=300, h=250))])
    assert req.app is None
    assert req.source is None
    assert req.regs is None


def test_backward_compat_model_validate_25_json():
    """既存の 2.5 フォーマット JSON を model_validate してもエラーにならない"""
    body = {
        "id": "req-1",
        "imp": [{"id": "imp-1", "banner": {"w": 300, "h": 250}, "bidfloor": 0.5}],
        "site": {"id": "pub-1", "domain": "example.com"},
        "device": {"ua": "Mozilla/5.0", "ip": "1.2.3.4"},
        "at": 2,
        "tmax": 80,
    }
    req = BidRequest.model_validate(body)
    assert req.id == "req-1"
    assert req.site.domain == "example.com"


# ── App（モバイルアプリ枠）テスト ───────────────────────────────

def test_app_object_parsed():
    """App オブジェクトが bundle / storeurl を含め正しくパースされる"""
    body = {
        "id": "req-app-1",
        "imp": [{"id": "imp-1", "bidfloor": 0.0}],
        "app": {
            "id": "app-001",
            "name": "My Game",
            "bundle": "com.example.mygame",
            "storeurl": "https://apps.apple.com/app/id123456",
        },
    }
    req = BidRequest.model_validate(body)
    assert req.app is not None
    assert req.app.bundle == "com.example.mygame"
    assert req.site is None


def test_app_and_site_both_optional():
    """app と site は両方 None でも BidRequest が生成できる"""
    req = BidRequest(imp=[Impression(id="imp-x")])
    assert req.app is None and req.site is None


# ── schain（SupplyChain）テスト ─────────────────────────────────

def test_schain_parsed_via_source_ext():
    """source.ext.schain が型付きで正しくパースされる"""
    body = {
        "id": "req-schain",
        "imp": [{"id": "imp-1"}],
        "source": {
            "tid": "tx-abc",
            "ext": {
                "schain": {
                    "complete": 1,
                    "ver": "1.0",
                    "nodes": [
                        {"asi": "exchange1.com", "sid": "1234", "hp": 1},
                        {"asi": "publisher.com", "sid": "pub-9", "hp": 1, "rid": "r-99"},
                    ],
                }
            },
        },
    }
    req = BidRequest.model_validate(body)
    schain = req.source.ext.schain
    assert schain.complete == 1
    assert len(schain.nodes) == 2
    assert schain.nodes[0].asi == "exchange1.com"
    assert schain.nodes[1].rid == "r-99"


def test_schain_node_hp_required():
    """SupplyChainNode は hp が必須フィールドである"""
    with pytest.raises(Exception):  # ValidationError
        SupplyChainNode(asi="x.com", sid="1")  # hp 欠落


# ── Regs (GPP) テスト ──────────────────────────────────────────

def test_regs_gpp_parsed():
    """regs.gpp と regs.gpp_sid が正しくパースされる"""
    body = {
        "id": "req-gpp",
        "imp": [{"id": "imp-1"}],
        "regs": {
            "coppa": 0,
            "gpp": "DBABMA~CPXxRfAPXxRfAAfKABENB-CgAAAAAAAAAAYgAAAAAAAA==",
            "gpp_sid": [2, 6],
        },
    }
    req = BidRequest.model_validate(body)
    assert req.regs.gpp is not None
    assert 2 in req.regs.gpp_sid
    assert req.regs.coppa == 0


# ── user.ext.eids テスト ────────────────────────────────────────

def test_user_ext_eids_parsed():
    """user.ext.eids が ExtendedId のリストとして型付きでパースされる"""
    body = {
        "id": "req-eids",
        "imp": [{"id": "imp-1"}],
        "user": {
            "id": "u-abc",
            "ext": {
                "eids": [
                    {
                        "source": "adserver.org",
                        "uids": [{"id": "TTD-uid-xyz", "atype": 1}],
                    },
                    {
                        "source": "liveramp.com",
                        "uids": [{"id": "LR-uid-abc", "atype": 3}],
                    },
                ]
            },
        },
    }
    req = BidRequest.model_validate(body)
    eids = req.user.ext.eids
    assert len(eids) == 2
    assert eids[0].source == "adserver.org"
    assert eids[0].uids[0].id == "TTD-uid-xyz"


# ── Bid.burl / Bid.lurl テスト ─────────────────────────────────

def test_bid_burl_lurl_round_trip():
    """Bid に burl / lurl を設定でき、model_dump に含まれる"""
    bid = Bid(
        impid="imp-1",
        price=1.5,
        burl="https://dsp.example.com/billing?price=${AUCTION_PRICE}",
        lurl="https://dsp.example.com/loss?reason=${AUCTION_LOSS}",
    )
    dumped = bid.model_dump(exclude_none=True)
    assert "burl" in dumped
    assert "lurl" in dumped
    assert "${AUCTION_PRICE}" in dumped["burl"]


# ── Imp.pmp / Deal テスト ──────────────────────────────────────

def test_pmp_deal_parsed():
    """imp.pmp.deals が Deal リストとして型付きでパースされる"""
    body = {
        "id": "req-pmp",
        "imp": [
            {
                "id": "imp-1",
                "bidfloor": 5.0,
                "pmp": {
                    "private_auction": 1,
                    "deals": [
                        {"id": "deal-123", "bidfloor": 10.0, "bidfloorcur": "USD"},
                    ],
                },
            }
        ],
    }
    req = BidRequest.model_validate(body)
    imp = req.imp[0]
    assert imp.pmp.private_auction == 1
    assert len(imp.pmp.deals) == 1
    assert imp.pmp.deals[0].id == "deal-123"
    assert imp.pmp.deals[0].bidfloor == 10.0


# ── Video 詳細フィールドテスト ─────────────────────────────────

def test_video_extended_fields():
    """Video に plcmt / skip / api / playbackmethod が設定できる"""
    body = {
        "id": "req-video",
        "imp": [
            {
                "id": "imp-1",
                "video": {
                    "mimes": ["video/mp4"],
                    "minduration": 5,
                    "maxduration": 30,
                    "protocols": [2, 3],
                    "w": 640,
                    "h": 480,
                    "startdelay": 0,
                    "plcmt": 1,
                    "skip": 1,
                    "skipafter": 5,
                    "api": [2],
                    "playbackmethod": [2],
                },
            }
        ],
    }
    req = BidRequest.model_validate(body)
    v = req.imp[0].video
    assert v.plcmt == 1
    assert v.skip == 1
    assert v.skipafter == 5
    assert 2 in v.api


# ── Device 拡張テスト ──────────────────────────────────────────

def test_device_geo_ifa_os_extended():
    """Device に geo / ifa / os / osv / make / model / lmt が設定できる"""
    body = {
        "id": "req-device",
        "imp": [{"id": "imp-1"}],
        "device": {
            "ua": "Mozilla/5.0",
            "ip": "1.2.3.4",
            "geo": {"lat": 35.6895, "lon": 139.6917, "country": "JPN"},
            "ifa": "6D92078A-8246-4BA4-AE5B-76104861E7DC",
            "make": "Apple",
            "model": "iPhone",
            "os": "iOS",
            "osv": "17.4",
            "lmt": 0,
            "connectiontype": 2,
        },
    }
    req = BidRequest.model_validate(body)
    d = req.device
    assert d.geo.country == "JPN"
    assert d.ifa == "6D92078A-8246-4BA4-AE5B-76104861E7DC"
    assert d.os == "iOS"
    assert d.lmt == 0


# ── model_dump exclude_none の検証 ─────────────────────────────

def test_model_dump_excludes_none_fields():
    """全新フィールドを除外した model_dump で最小 JSON 出力になる"""
    req = BidRequest(imp=[Impression(id="imp-1")])
    dumped = req.model_dump(exclude_none=True)
    assert "app" not in dumped
    assert "source" not in dumped
    assert "regs" not in dumped
    assert "imp" in dumped
