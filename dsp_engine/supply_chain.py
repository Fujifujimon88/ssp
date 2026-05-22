"""
dsp_engine schain 構造検証。

外部エクスチェンジ受信時に BidRequest.source.ext.schain（SupplyChain）の
構造を検証する。外部 I/O を一切含まない純粋関数のみで構成し、入札パス内から
安全に呼べる（低遅延・高 QPS 制約のため）。

判定: PASS / WARN（警告ログのみ）/ REJECT（ノービッド扱い）。
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from auction.openrtb import SupplyChain


class SchainVerdict(Enum):
    PASS = "pass"
    WARN = "warn"      # complete=0 等。拒否はしない
    REJECT = "reject"  # 構造不正・なりすまし疑い


@dataclass
class SchainResult:
    verdict: SchainVerdict
    reason: str        # ログ・統計用の理由文字列
    node_count: int = 0


def verify_schain(
    schain: Optional[SupplyChain],
    exchange_asi: str,
    allowed_asi_domains: list[str],
    strict: bool = False,
) -> SchainResult:
    """受信した schain の構造を検証する（外部 I/O なし・純粋関数）。

    Args:
        schain: BidRequest.source.ext.schain（未送信なら None）。
        exchange_asi: 送信元エクスチェンジの asi ドメイン。
        allowed_asi_domains: 許可する asi のリスト（空なら無制限）。
        strict: True=検証失敗で REJECT、False=WARN 止まり。

    exchange_asi は送信元エクスチェンジ自身の asi ドメイン。SSP 連携画面の
    「接続名」とは別物（接続名は任意文字列のため asi に流用してはならない）。
    未設定（空文字/None）のときは最終ノード asi 一致を検証できないためスキップする。

    判定（REJECT 条件を優先評価）:
      1. schain 未送信         → strict ? REJECT : WARN
      2. nodes 空              → REJECT
      3. 最終ノード asi 不一致  → REJECT（exchange_asi 未設定時はスキップ）
      4. 許可リスト外の asi     → strict ? REJECT : WARN
      5. complete=0            → WARN
      6. 上記なし              → PASS
    """
    if schain is None:
        if strict:
            return SchainResult(SchainVerdict.REJECT, "schain missing (schain_required)")
        return SchainResult(SchainVerdict.WARN, "schain missing")

    nodes = schain.nodes or []
    if not nodes:
        return SchainResult(SchainVerdict.REJECT, "schain has no nodes", 0)

    expected_asi = (exchange_asi or "").lower()
    if expected_asi:
        last_asi = (nodes[-1].asi or "").lower()
        if last_asi != expected_asi:
            return SchainResult(
                SchainVerdict.REJECT,
                f"last node asi {last_asi!r} != exchange {expected_asi!r}",
                len(nodes),
            )

    if allowed_asi_domains:
        allowed = {d.lower() for d in allowed_asi_domains}
        outside = [n.asi for n in nodes if (n.asi or "").lower() not in allowed]
        if outside:
            verdict = SchainVerdict.REJECT if strict else SchainVerdict.WARN
            return SchainResult(verdict, f"asi not in allowlist: {outside}", len(nodes))

    if schain.complete != 1:
        return SchainResult(SchainVerdict.WARN, "schain incomplete (complete=0)", len(nodes))

    return SchainResult(SchainVerdict.PASS, "ok", len(nodes))


def verifiable_nodes(
    schain: Optional[SupplyChain], exchange_asi: str
) -> list:
    """sellers.json 突合の対象にできる schain ノードを返す（純粋関数）。

    各 asi の sellers.json は「その asi が直接取引する seller」だけを列挙する。
    よって突合できるのは asi が当該エクスチェンジ（exchange_asi）と一致する
    ノードのみ。上流ノードの sid は上流側の sellers.json に属するため、
    当該エクスチェンジの sellers.json で検証してはならない（多段 schain の
    誤 no-bid を防ぐ）。

    exchange_asi 未設定（空文字/None）のときは突合不能なので空リストを返す。
    """
    if schain is None or not schain.nodes or not exchange_asi:
        return []
    target = exchange_asi.lower()
    return [n for n in schain.nodes if (n.asi or "").lower() == target]


def extract_schain(bid_request) -> Optional[SupplyChain]:
    """BidRequest.source.ext.schain を安全に取得する（無ければ None）。"""
    try:
        return bid_request.source.ext.schain
    except AttributeError:
        return None
