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
    verifiable_nodes,
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


# ── verify_schain: exchange_asi 未設定（接続名を asi と誤用しない）──

def test_verify_schain_skips_last_node_check_when_asi_empty():
    """exchange_asi 未設定（空文字）→ 最終ノード asi 一致チェックをスキップ。

    指摘1 の回帰防止: 接続名（任意文字列）を asi として渡してはならない。
    asi が無いときは検証不能なので REJECT せず後続チェックへ進む。
    """
    sc = _schain([_node("upstream.com"), _node("exchange1.com")], complete=1)
    r = verify_schain(sc, exchange_asi="", allowed_asi_domains=[], strict=False)
    assert r.verdict == SchainVerdict.PASS


def test_verify_schain_empty_asi_does_not_reject_mismatch():
    """exchange_asi 空 + strict=True でも最終ノード不一致で REJECT しない（検証不能）"""
    sc = _schain([_node("anything.com")], complete=1)
    r = verify_schain(sc, exchange_asi="", allowed_asi_domains=[], strict=True)
    assert r.verdict != SchainVerdict.REJECT


def test_verify_schain_empty_asi_still_rejects_empty_nodes():
    """exchange_asi 空でも nodes 空は構造不正なので REJECT のまま"""
    r = verify_schain(SupplyChain(complete=1, nodes=[]), exchange_asi="", allowed_asi_domains=[])
    assert r.verdict == SchainVerdict.REJECT


# ── verifiable_nodes: sellers.json 突合対象ノードの絞り込み ──────

def test_verifiable_nodes_only_matching_asi():
    """指摘2 の回帰防止: 突合対象は asi が当該エクスチェンジと一致するノードのみ"""
    sc = _schain([_node("upstream.com", sid="up-1"), _node("exchange1.com", sid="ex-1")])
    nodes = verifiable_nodes(sc, exchange_asi="Exchange1.com")  # 大小無視
    assert [n.sid for n in nodes] == ["ex-1"]


def test_verifiable_nodes_empty_when_asi_unset():
    """exchange_asi 未設定 → 突合対象なし（突合スキップ）"""
    sc = _schain([_node("exchange1.com")])
    assert verifiable_nodes(sc, exchange_asi="") == []


def test_verifiable_nodes_none_schain():
    """schain なし → 空リスト"""
    assert verifiable_nodes(None, exchange_asi="exchange1.com") == []


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
