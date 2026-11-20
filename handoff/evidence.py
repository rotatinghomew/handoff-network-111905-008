"""演练证据分级。

只有收到对方确认才算交接成功；未观察到的转接不自动算失败。
按资料里的接通区间和缺观测声明区分：

- 已知 known：同班次、权限覆盖的 confirmed 试拨；
- 可能 possible：只有 missing 缺观测声明，区间为可能确认时间；
- 未知 unknown：该班次该权限没有任何记录；
- 已知失败 failed：同班次、权限覆盖的 failure 记录。

白班的成功试拨不能替夜班签收；不同授权（权限不足）的记录不能凑成一次成功。
共享的演练结果可支持多个同条件班次。
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Drill
from .routes import Network, Traversal, shift_of

KNOWN = "known"
POSSIBLE = "possible"
UNKNOWN = "unknown"
FAILED = "failed"

LABELS = {
    KNOWN: "已知",
    POSSIBLE: "可能",
    UNKNOWN: "未知",
    FAILED: "已知失败",
}

# 严重度：用于路线汇总（取各段最差）。
_WORST = {KNOWN: 0, POSSIBLE: 1, UNKNOWN: 2, FAILED: 3}

# 授权级别：高权限的演练记录可证明低权限内容的可达，反之不能。
_CLEARANCE = {"普通": 1, "需保密": 2}


def covers(holder: frozenset[str], required: frozenset[str]) -> bool:
    """holder 的授权是否足以证明 required 内容的转接。"""

    level = max((_CLEARANCE.get(perm, 0) for perm in holder), default=0)
    return all(_CLEARANCE.get(perm, 0) <= level for perm in required)


@dataclass(frozen=True)
class Evidence:
    status: str
    interval: tuple[int, int] | None = None  # possible 时的可能确认区间
    source: str | None = None  # 依据的演练记录 id

    @property
    def label(self) -> str:
        return LABELS[self.status]


def link_evidence(
    drills: list[Drill], link_id: str, shift: str, required: frozenset[str]
) -> Evidence:
    """单条边在某班次、某内容权限下的证据级别。"""

    covering = [
        drill
        for drill in drills
        if drill.link == link_id
        and drill.shift == shift
        and covers(drill.permission, required)
    ]
    for drill in covering:
        if drill.result == "confirmed":
            return Evidence(KNOWN, (drill.start, drill.end), drill.id)
    for drill in covering:
        if drill.result == "failure":
            return Evidence(FAILED, (drill.start, drill.end), drill.id)
    for drill in covering:
        if drill.result == "missing":
            return Evidence(POSSIBLE, (drill.start, drill.end), drill.id)
    return Evidence(UNKNOWN)


def route_evidence(
    drills: list[Drill],
    net: Network,
    traversals: list[Traversal],
    required: frozenset[str],
) -> Evidence:
    """路线证据：逐段按其转接开始时刻认定日夜班，取最差。

    不能用一条白班的成功试拨替一个夜班签收；不同授权岗位不能凑成一次成功。
    """

    worst = Evidence(KNOWN)
    for traversal in traversals:
        edge = net.edges[traversal.edge]
        if edge.origin != "link":
            candidate = Evidence(UNKNOWN)  # 新增安排尚无演练记录
        else:
            candidate = link_evidence(
                drills, traversal.edge, shift_of(traversal.start), required
            )
        if _WORST[candidate.status] > _WORST[worst.status]:
            worst = candidate
    return worst


def trial_chains(drills: list[Drill]) -> dict[str, list[Drill]]:
    """同一 trial 标同一消息试拨链：整链 confirmed 才算该链确认。"""

    chains: dict[str, list[Drill]] = {}
    for drill in drills:
        chains.setdefault(drill.trial, []).append(drill)
    return chains
