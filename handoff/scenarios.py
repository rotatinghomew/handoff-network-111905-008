"""约定的单点中断情景。

对每一类约定的单点（共用电话 channel、必须值班的关口 gateway、岗位本身）
计算服务可达范围、承诺能否赶在截止之前。同一次中断下的所有服务必须用
同一份排班和占用事实：按保留承诺优先、截止早者优先的顺序统一排班；
已不可能赶在截止前的服务立即转入人工联络清单，不再占用联络能力。

不用网络整体的平均连通率证明某个班次的服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Data, Service
from .routes import (
    Edge,
    Network,
    Traversal,
    completion_of,
    enumerate_routes,
    meets_deadline,
    occupy,
    schedule_route,
)

# 服务在情景下的结局
MET = "met"  # 可赶在截止前
LATE = "late"  # 有路线但赶不上约定时间
UNREACHABLE = "unreachable"  # 无可用路线

REASON_LABELS = {
    LATE: "无法赶上约定时间",
    UNREACHABLE: "无可用路线",
}


@dataclass(frozen=True)
class Factor:
    """一个约定的单点：kind ∈ channel / gateway / post。"""

    kind: str
    name: str

    @property
    def label(self) -> str:
        kinds = {"channel": "电话", "gateway": "关口岗位", "post": "岗位"}
        return f"{kinds[self.kind]} {self.name}"


@dataclass
class Outcome:
    service: str
    status: str
    route: tuple[Edge, ...] = ()
    traversals: list[Traversal] = field(default_factory=list)
    completion: int | None = None

    @property
    def reason(self) -> str | None:
        return REASON_LABELS.get(self.status)


@dataclass
class ScenarioResult:
    factor: Factor | None  # None 表示正常排班
    outcomes: dict[str, Outcome]

    def lost_vs(self, baseline: "ScenarioResult") -> list[str]:
        """相对正常排班，本情景下由可赶在截止前变为不能的服务。"""

        lost = []
        for service_id, outcome in self.outcomes.items():
            before = baseline.outcomes[service_id]
            if before.status == MET and outcome.status != MET:
                lost.append(service_id)
        return lost


def all_factors(data: Data) -> list[Factor]:
    """约定的各类单点：出现过的 channel、gateway，以及联络图上的岗位。"""

    channels = sorted({link.channel for link in data.links.values()})
    gateways = sorted({link.gateway for link in data.links.values() if link.gateway})
    posts = sorted(
        {link.source for link in data.links.values()}
        | {link.target for link in data.links.values()}
    )
    return (
        [Factor("channel", name) for name in channels]
        + [Factor("gateway", name) for name in gateways]
        + [Factor("post", name) for name in posts]
    )


def dead_links(data: Data, factor: Factor) -> frozenset[str]:
    """该单点失效时不可用的联络段。"""

    dead = set()
    for link in data.links.values():
        if factor.kind == "channel" and link.channel == factor.name:
            dead.add(link.id)
        elif factor.kind == "gateway" and link.gateway == factor.name:
            dead.add(link.id)
        elif factor.kind == "post" and factor.name in (
            link.source,
            link.target,
            link.gateway,
        ):
            dead.add(link.id)
    return frozenset(dead)


def _priority(service: Service) -> tuple[int, int, str]:
    # 保留承诺优先，其次截止早者，最后按 id 稳定。
    return (0 if service.reserve else 1, service.deadline, service.id)


def run_scenario(
    data: Data,
    factor: Factor | None,
    adopted: frozenset[str] = frozenset(),
    extra_busy: dict[str, list[tuple[int, int, str | None]]] | None = None,
) -> ScenarioResult:
    """在一份统一占用事实下为所有服务排班。

    每项服务独立选路（正常排班与各单点失效情况允许用不同联络安排），
    但同一情景内共享人员占用；赶不上截止的服务转入人工清单、不占能力。
    """

    dead = dead_links(data, factor) if factor else frozenset()
    net = Network.build(data, dead=dead, adopted=adopted)
    busy: dict[str, list[tuple[int, int, str | None]]] = {
        person: list(intervals) for person, intervals in (extra_busy or {}).items()
    }
    # 人员资源的已采纳选项：该人员在窗口内全程候命，只承接本选项的边。
    for option_id in adopted:
        option = data.options[option_id]
        if option.resource in data.persons:
            busy.setdefault(option.resource, []).append(
                (option.start, option.end, option.id)
            )
    outcomes: dict[str, Outcome] = {}
    for service in sorted(data.services.values(), key=_priority):
        best_late: tuple[int, tuple[Edge, ...], list[Traversal]] | None = None
        chosen: tuple[tuple[Edge, ...], list[Traversal]] | None = None
        for route in enumerate_routes(net, service):
            traversals = schedule_route(net, service, route, busy)
            if traversals is None:
                continue
            completion = completion_of(traversals)
            if meets_deadline(service, completion):
                if chosen is None or completion < completion_of(chosen[1]):
                    chosen = (route, traversals)
            elif best_late is None or completion < best_late[0]:
                best_late = (completion, route, traversals)
        if chosen is not None:
            route, traversals = chosen
            occupy(busy, net, traversals)
            outcomes[service.id] = Outcome(
                service=service.id,
                status=MET,
                route=route,
                traversals=traversals,
                completion=completion_of(traversals),
            )
        elif best_late is not None:
            completion, route, traversals = best_late
            outcomes[service.id] = Outcome(
                service=service.id,
                status=LATE,
                route=route,
                traversals=traversals,
                completion=completion,
            )
        else:
            outcomes[service.id] = Outcome(service=service.id, status=UNREACHABLE)
    return ScenarioResult(factor=factor, outcomes=outcomes)


def factor_impacts(
    data: Data, adopted: frozenset[str] = frozenset()
) -> tuple[ScenarioResult, list[tuple[Factor, ScenarioResult, list[str]]]]:
    """正常排班 + 各单点情景；返回基准与 (单点, 结果, 失去的服务) 列表。"""

    baseline = run_scenario(data, None, adopted=adopted)
    impacts = []
    for factor in all_factors(data):
        result = run_scenario(data, factor, adopted=adopted)
        impacts.append((factor, result, result.lost_vs(baseline)))
    return baseline, impacts


def rank_breakpoints(
    data: Data, impacts: list[tuple[Factor, ScenarioResult, list[str]]]
) -> list[tuple[Factor, int, int, list[str]]]:
    """最值得消除的共同断点：按失去的保留承诺数、失去的服务总数排序。"""

    ranked = []
    for factor, _result, lost in impacts:
        reserved = sum(1 for service_id in lost if data.services[service_id].reserve)
        ranked.append((factor, reserved, len(lost), lost))
    ranked.sort(key=lambda item: (-item[1], -item[2], item[0].label))
    return [item for item in ranked if item[2] > 0]
