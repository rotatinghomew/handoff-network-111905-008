"""新增联络安排（options）比较。

待选情景可能新增一个电话（试拨名额资源），也可能把一个人从单向转告改为
全程候命（人员资源）——后者占用他本来能处理的另一处到场服务。

约束：
- 同一试拨名额每时段仅一份：资源相同且窗口重叠的选项互斥；
- 人员资源选项在该人员值班窗口内占用他；若该岗 on_site 已承诺到场，
  采纳即消耗这份到场承诺（记为代价，不静默通过）；
- 保留承诺（reserve）已有责任归属，不能被新安排占用。

比较产出：各可行组合能消除哪些成对断点、哪些服务仍不宜提前答应。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Data, Option
from .routes import Network, enumerate_routes, independent, meets_deadline, schedule_route, completion_of


@dataclass(frozen=True)
class OptionConflict:
    first: str
    second: str
    reason: str


@dataclass(frozen=True)
class OptionCost:
    option: str
    reason: str


def is_person_resource(data: Data, option: Option) -> bool:
    return option.resource in data.persons


def option_conflicts(data: Data) -> list[OptionConflict]:
    """试拨名额每时段仅一份：同名额且窗口重叠的选项互斥。"""

    conflicts = []
    options = list(data.options.values())
    for i, first in enumerate(options):
        for second in options[i + 1 :]:
            if first.resource != second.resource:
                continue
            if is_person_resource(data, first):
                continue  # 人员占用在占用事实里另行判断
            if first.start < second.end and second.start < first.end:
                conflicts.append(
                    OptionConflict(
                        first=first.id,
                        second=second.id,
                        reason=f"试拨名额 {first.resource} 每时段仅一份",
                    )
                )
    return conflicts


def option_costs(data: Data) -> list[OptionCost]:
    """人员资源选项的代价：改为全程候命所消耗的既有承诺。"""

    costs = []
    for option in data.options.values():
        if not is_person_resource(data, option):
            continue
        person = option.resource
        for duty in data.duties.values():
            if duty.person != person:
                continue
            if duty.start <= option.start and option.end <= duty.end and duty.on_site:
                costs.append(
                    OptionCost(
                        option=option.id,
                        reason=(
                            f"{person} 在 {option.start // 60:02d}:{option.start % 60:02d}-"
                            f"{option.end // 60:02d}:{option.end % 60:02d} 改为全程候命，"
                            f"消耗 {duty.id} 已承诺的到场服务"
                        ),
                    )
                )
    return costs


def feasible_sets(data: Data) -> list[frozenset[str]]:
    """所有满足互斥约束的采纳组合（含空集）。"""

    conflicts = option_conflicts(data)
    ids = sorted(data.options)
    sets: list[frozenset[str]] = []
    for mask in range(1 << len(ids)):
        chosen = frozenset(ids[i] for i in range(len(ids)) if mask & (1 << i))
        if any(
            conflict.first in chosen and conflict.second in chosen
            for conflict in conflicts
        ):
            continue
        sets.append(chosen)
    return sets


@dataclass
class ServiceBackup:
    service: str
    timely_routes: int
    independent_pair: bool
    single_points: frozenset[str]  # 出现在每条可及时路线上的因素标签


def backup_profile(data: Data, adopted: frozenset[str]) -> dict[str, ServiceBackup]:
    """每项服务在某采纳组合下的及时路线与独立备援情况。"""

    net = Network.build(data, adopted=adopted)
    profiles: dict[str, ServiceBackup] = {}
    for service in data.services.values():
        timely = []
        for route in enumerate_routes(net, service):
            traversals = schedule_route(net, service, route)
            if traversals is not None and meets_deadline(
                service, completion_of(traversals)
            ):
                timely.append(route)
        factors = [net.factors_of(route) for route in timely]
        pair = any(
            independent(first, second)
            for i, first in enumerate(factors)
            for second in factors[i + 1 :]
        )
        if factors:
            common_channels = set.intersection(*(set(f.channels) for f in factors))
            common_gateways = set.intersection(*(set(f.gateways) for f in factors))
            singles = frozenset(
                {f"电话 {name}" for name in common_channels}
                | {f"关口岗位 {name}" for name in common_gateways}
            )
        else:
            singles = frozenset()
        profiles[service.id] = ServiceBackup(
            service=service.id,
            timely_routes=len(timely),
            independent_pair=pair,
            single_points=singles,
        )
    return profiles


@dataclass
class OptionComparison:
    conflicts: list[OptionConflict]
    costs: list[OptionCost]
    baseline: dict[str, ServiceBackup]
    by_set: dict[frozenset[str], dict[str, ServiceBackup]] = field(default_factory=dict)

    def eliminated(self, adopted: frozenset[str]) -> dict[str, frozenset[str]]:
        """相对不采纳，该组合为各服务消除的共同断点。"""

        eliminated = {}
        after = self.by_set[adopted]
        for service_id, before in self.baseline.items():
            gone = before.single_points - after[service_id].single_points
            if gone:
                eliminated[service_id] = frozenset(gone)
        return eliminated

    def not_promisable(self, adopted: frozenset[str]) -> list[str]:
        """仍不宜提前答应的服务：没有可及时路线，或没有独立备援。"""

        profile = self.by_set[adopted]
        return sorted(
            service_id
            for service_id, backup in profile.items()
            if backup.timely_routes == 0 or not backup.independent_pair
        )


def compare_options(data: Data) -> OptionComparison:
    baseline = backup_profile(data, frozenset())
    comparison = OptionComparison(
        conflicts=option_conflicts(data),
        costs=option_costs(data),
        baseline=baseline,
    )
    for adopted in feasible_sets(data):
        comparison.by_set[adopted] = backup_profile(data, adopted)
    return comparison
