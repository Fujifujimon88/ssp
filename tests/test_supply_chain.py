"""
dsp_engine schain 構造検証のユニットテスト（優先タスク #3 Phase A）

verify_schain は入札パス内から呼ぶ純粋関数（外部 I/O なし）。
実行: cd ssp_platform && pytest tests/test_supply_chain.py -v
"""
from auction.openrtb import (
    BidRequest,
    Impression,
    Source,
    SourceExt,
    SupplyChain,
    SupplyChainNode,
)
from dsp_engine.supply_chain import (
    SchainResult,
    SchainVerdict,
    extract_schain,
    verify_schain,
)


def _node(asi: str, sid: str = "s1", hp: int = 1) -> SupplyChainNode:
    return SupplyChainNode(asi=asi, sid=sid, hp=hp)


def _schain(nodes: list[SupplyChainNode], complete: int = 1) -> SupplyChain:
    return SupplyChain(complete=complete, nodes=nodes)


# ── verify_schain ──────────────────────────────────────────────

def test_verify_schain_pass_complete():
    """complete=1・最終ノード一致・許可リスト無制限 → PASS"""
    sc = _schain([_node("upstream.com"), _node("exchange1.com")], complete=1)
    r = verify_schain(sc, exchange_asi="exchange1.com", allowed_asi_domains=[], strict=False)
    assert isinstance(r, SchainResult)
    assert r.verdict == SchainVerdict.PASS


def test_verify_schain_warn_incomplete():
    """complete=0（不完全チェーン）→ WARN（拒否はしない）"""
    sc = _schain([_node("exchange1.com")], complete=0)
    r = verify_schain(sc, "exchange1.com", [], strict=False)
    assert r.verdict == SchainVerdict.WARN


def test_verify_schain_reject_empty_nodes():
    """nodes が空 → REJECT（構造不正）"""
    sc = SupplyChain(complete=1, nodes=[])
    r = verify_schain(sc, "exchange1.com", [], strict=False)
    assert r.verdict == SchainVerdict.REJECT


def test_verify_schain_reject_last_node_mismatch():
    """最終ノードの asi が送信元エクスチェンジと不一致 → REJECT"""
    sc = _schain([_node("other.com")], complete=1)
    r = verify_schain(sc, "exchange1.com", [], strict=False)
    assert r.verdict == SchainVerdict.REJECT


def test_verify_schain_warn_asi_not_in_allowed():
    """許可リスト外の asi（strict=False）→ WARN"""
    sc = _schain([_node("rogue.com"), _node("exchange1.com")], complete=1)
    r = verify_schain(sc, "exchange1.com", allowed_asi_domains=["exchange1.com"], strict=False)
    assert r.verdict == SchainVerdict.WARN


def test_verify_schain_reject_asi_strict():
    """許可リスト外の asi（strict=True）→ REJECT"""
    sc = _schain([_node("rogue.com"), _node("exchange1.com")], complete=1)
    r = verify_schain(sc, "exchange1.com", allowed_asi_domains=["exchange1.com"], strict=True)
    assert r.verdict == SchainVerdict.REJECT


def test_verify_schain_none_strict():
    """schain 未送信・strict=True（schain_required）→ REJECT"""
    r = verify_schain(None, "exchange1.com", [], strict=True)
    assert r.verdict == SchainVerdict.REJECT


def test_verify_schain_none_not_strict():
    """schain 未送信・strict=False → WARN"""
    r = verify_schain(None, "exchange1.com", [], strict=False)
    assert r.verdict == SchainVerdict.WARN


# ── extract_schain ─────────────────────────────────────────────

def test_extract_schain_from_bid_request():
    """BidRequest.source.ext.schain を安全に取得できる"""
    sc = _schain([_node("exchange1.com")])
    req = BidRequest(
        imp=[Impression(id="i1")],
        source=Source(ext=SourceExt(schain=sc)),
    )
    assert extract_schain(req) is sc


def test_extract_schain_missing_source():
    """source が無い BidRequest からは None を返す（例外を出さない）"""
    req = BidRequest(imp=[Impression(id="i1")])
    assert extract_schain(req) is None
