"""联络路线认定。

认定规则（来自 DATA.md 与业务约定）：

- 消息在一条联络段上的占用为 [t, t+minutes)，联络与确认共占该段总分钟；
  开始及完成均须在发送班次、接收班次和通路时段内（时段左闭右开）。
- 服务内容的权限须被每一段边及两端岗位的权限覆盖（岗位允许转交的内容）。
- 相同人员同一时刻只承接一项；同一次中断下的所有服务共用同一份占用事实。
- target 须在 deadline 前签收：完成时刻必须严格早于 deadline。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Data, Link, Option, Service

# 白班 / 夜班演练窗口分界：19:00 起为夜班。
SHIFT_BOUNDARY = 19 * 60


def shift_of(start: int) -> str:
    return "night" if start >= SHIFT_BOUNDARY else "day"


@dataclass(frozen=True)
class Edge:
    """统一的可用联络段：既有 links 或已采纳的 options。"""

    id: str
    source: str
    target: str
    start: int
    end: int
    channel: str
    gateway: str | None
    minutes: int
    permission: frozenset[str]
    origin: str  # "link" 或对应 option id


def edge_of_link(link: Link) -> Edge:
    return Edge(
        id=link.id,
        source=link.source,
        target=link.target,
        start=link.start,
        end=link.end,
        channel=link.channel,
        gateway=link.gateway,
        minutes=link.minutes,
        permission=link.permission,
        origin="link",
    )


def edge_of_option(option: Option) -> Edge:
    return Edge(
        id=option.id,
        source=option.source,
        target=option.target,
        start=option.start,
        end=option.end,
        channel=option.channel,
        gateway=None,
        minutes=option.minutes,
        permission=option.permission,
        origin=option.id,
    )


@dataclass(frozen=True)
class Factors:
    """一条路线依赖的故障因素。"""

    channels: frozenset[str]
    gateways: frozenset[str]
    posts: frozenset[str]
    persons: frozenset[str]
    edges: frozenset[str]


@dataclass(frozen=True)
class Traversal:
    edge: str
    source: str
    target: str
    start: int
    end: int


@dataclass
class Network:
    """某一情景下的可用联络图（links 减去失效边，加上已采纳 options）。"""

    data: Data
    edges: dict[str, Edge]
    adjacency: dict[str, list[Edge]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        data: Data,
        dead: frozenset[str] = frozenset(),
        adopted: frozenset[str] = frozenset(),
    ) -> "Network":
        edges: dict[str, Edge] = {}
        for link in data.links.values():
            if link.id not in dead:
                edges[link.id] = edge_of_link(link)
        for option_id in adopted:
            option = data.options[option_id]
            edges[option.id] = edge_of_option(option)
        adjacency: dict[str, list[Edge]] = {}
        for edge in edges.values():
            adjacency.setdefault(edge.source, []).append(edge)
        for edge_list in adjacency.values():
            edge_list.sort(key=lambda edge: (edge.start, edge.id))
        return cls(data=data, edges=edges, adjacency=adjacency)

    def factors_of(self, route: tuple[Edge, ...]) -> Factors:
        channels, gateways, posts, persons, edge_ids = set(), set(), set(), set(), set()
        for edge in route:
            channels.add(edge.channel)
            if edge.gateway:
                gateways.add(edge.gateway)
            posts.update((edge.source, edge.target))
            persons.add(self.data.person_of(edge.source))
            persons.add(self.data.person_of(edge.target))
            edge_ids.add(edge.id)
        return Factors(
            channels=frozenset(channels),
            gateways=frozenset(gateways),
            posts=frozenset(posts),
            persons=frozenset(persons),
            edges=frozenset(edge_ids),
        )


def independent(first: Factors, second: Factors) -> bool:
    """两条路线互为独立备援：不共用电话 channel、值班关口 gateway 或同一段边。

    经过不同人但依赖同一部电话或同一必须值班岗位的路线对，不能证明
    任一失联时仍可达。
    """

    return not (
        first.channels & second.channels
        or first.gateways & second.gateways
        or first.edges & second.edges
    )


def enumerate_routes(
    net: Network, service: Service, max_hops: int = 3
) -> list[tuple[Edge, ...]]:
    """按权限与岗位允许转交的内容枚举有向路线（不重复经过岗位）。"""

    routes: list[tuple[Edge, ...]] = []

    def allowed(edge: Edge) -> bool:
        if not service.permission <= edge.permission:
            return False
        for post in (edge.source, edge.target):
            if not service.permission <= net.data.duties[post].permission:
                return False
        return True

    def walk(post: str, path: list[Edge], seen: set[str]) -> None:
        if post == service.target and path:
            routes.append(tuple(path))
            return
        if len(path) >= max_hops:
            return
        for edge in net.adjacency.get(post, ()):  # 时间先后由排班判定
            if edge.target in seen or not allowed(edge):
                continue
            walk(edge.target, [*path, edge], seen | {edge.target})

    walk(service.source, [], {service.source})
    return routes


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def earliest_slot(
    arrival: int,
    edge: Edge,
    net: Network,
    busy: dict[str, list[tuple[int, int, str | None]]],
) -> int | None:
    """在边上找最早可行开始时刻。

    [t, t+minutes) 须落在边窗口与两端岗位班次内，且两端人员在该段内
    没有其它承接（busy 中带 edge 标记的占用对本边豁免，用于候命选项自身）。
    """

    duties = net.data.duties
    start = max(arrival, edge.start, duties[edge.source].start, duties[edge.target].start)
    latest = min(edge.end, duties[edge.source].end, duties[edge.target].end) - edge.minutes
    persons = {duties[edge.source].person, duties[edge.target].person}
    t = start
    while t <= latest:
        conflict_end: int | None = None
        for person in persons:
            for busy_start, busy_end, exempt in busy.get(person, ()):
                if exempt == edge.id:
                    continue
                if _overlaps(t, t + edge.minutes, busy_start, busy_end):
                    conflict_end = max(conflict_end or busy_end, busy_end)
        if conflict_end is None:
            return t
        t = max(t + 1, conflict_end)
    return None


def schedule_route(
    net: Network,
    service: Service,
    route: tuple[Edge, ...],
    busy: dict[str, list[tuple[int, int, str | None]]] | None = None,
) -> list[Traversal] | None:
    """从 release 起逐段尽量提前；任一一段排不下则返回 None。"""

    busy = busy or {}
    duties = net.data.duties
    traversals: list[Traversal] = []
    arrival = service.release
    for edge in route:
        slot = earliest_slot(arrival, edge, net, busy)
        if slot is None:
            return None
        traversals.append(
            Traversal(
                edge=edge.id,
                source=edge.source,
                target=edge.target,
                start=slot,
                end=slot + edge.minutes,
            )
        )
        arrival = slot + edge.minutes
    return traversals


def occupy(
    busy: dict[str, list[tuple[int, int, str | None]]],
    net: Network,
    traversals: list[Traversal],
    exempt_edge: str | None = None,
) -> None:
    """把一组转接占用写入同一份占用事实（两端人员全程占用）。"""

    edge_by_id = net.edges
    for traversal in traversals:
        edge = edge_by_id[traversal.edge]
        for post in (edge.source, edge.target):
            person = net.data.person_of(post)
            busy.setdefault(person, []).append(
                (traversal.start, traversal.end, exempt_edge)
            )
    for intervals in busy.values():
        intervals.sort()


def completion_of(traversals: list[Traversal]) -> int:
    return traversals[-1].end


def meets_deadline(service: Service, completion: int) -> bool:
    """target 须在 deadline 前签收：完成时刻严格早于 deadline。"""

    return completion < service.deadline
