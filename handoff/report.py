"""换班前联络保障报告。

用法：python3 -m handoff [fixtures 目录]

分节：
1. 联络路线认定（时刻、权限、占用）
2. 独立备援与成对断点
3. 单点中断情景（同一中断下统一占用）
4. 演练证据分级
5. 真实换班事件与责任归属
6. 新增联络安排比较
7. 复盘（按班次）
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import desk, evidence, options, scenarios
from .model import Data, fmt_time, load
from .routes import (
    Network,
    completion_of,
    enumerate_routes,
    independent,
    meets_deadline,
    schedule_route,
    shift_of,
)


def _route_text(net: Network, route) -> str:
    hops = " → ".join(edge.id for edge in route)
    factors = net.factors_of(route)
    shared = []
    if factors.channels:
        shared.append("电话 " + "/".join(sorted(factors.channels)))
    if factors.gateways:
        shared.append("关口 " + "/".join(sorted(factors.gateways)))
    return f"{hops}（{'，'.join(shared) or '无共享因素'}）"


def section_routes(data: Data) -> list[str]:
    lines = ["== 1. 联络路线认定（按消息真正到达的先后与岗位允许转交的内容） =="]
    net = Network.build(data)
    for service in data.services.values():
        mark = "保留承诺" if service.reserve else "未承诺"
        lines.append(
            f"{service.id} {service.source}→{service.target} "
            f"{'/'.join(sorted(service.permission))} "
            f"{fmt_time(service.release)} 放出，须早于 {fmt_time(service.deadline)} 签收 [{mark}]"
        )
        routes = enumerate_routes(net, service)
        if not routes:
            lines.append("  无任何满足权限与时刻的路线 —— 不宜提前答应")
            continue
        for route in routes:
            traversals = schedule_route(net, service, route)
            if traversals is None:
                lines.append(f"  路线 {_route_text(net, route)}：时段排不开")
                continue
            completion = completion_of(traversals)
            timely = meets_deadline(service, completion)
            plan = "，".join(
                f"{t.edge} {fmt_time(t.start)}-{fmt_time(t.end)}" for t in traversals
            )
            verdict = "可赶在截止前" if timely else "完成即/晚于截止，不计及时"
            lines.append(
                f"  路线 {_route_text(net, route)}\n"
                f"    {plan} → {fmt_time(completion)} 完成，{verdict}"
            )
    return lines


def section_independence(data: Data) -> list[str]:
    lines = ["== 2. 独立备援与成对断点 =="]
    net = Network.build(data)
    for service in data.services.values():
        timely = []
        for route in enumerate_routes(net, service):
            traversals = schedule_route(net, service, route)
            if traversals is not None and meets_deadline(
                service, completion_of(traversals)
            ):
                timely.append(route)
        if not timely:
            lines.append(f"{service.id}：没有可赶在截止前的路线")
            continue
        factors = [net.factors_of(route) for route in timely]
        names = ["+".join(edge.id for edge in route) for route in timely]
        if len(timely) == 1:
            only = factors[0]
            points = sorted(only.channels | only.gateways)
            lines.append(
                f"{service.id}：仅 {names[0]} 一条及时路线，单点 {('/'.join(points)) or '无'} "
                "失效即失去全部备援"
            )
            continue
        lines.append(f"{service.id}：{len(timely)} 条及时路线")
        for i in range(len(timely)):
            for j in range(i + 1, len(timely)):
                if independent(factors[i], factors[j]):
                    lines.append(f"  {names[i]} 与 {names[j]}：互为独立备援")
                else:
                    shared = sorted(
                        (factors[i].channels & factors[j].channels)
                        | (factors[i].gateways & factors[j].gateways)
                    )
                    lines.append(
                        f"  {names[i]} 与 {names[j]}：看似两条，共用 "
                        f"{'/'.join(shared)} —— 任一失联不能证明仍可达（成对断点）"
                    )
    return lines


def section_scenarios(data: Data) -> list[str]:
    lines = ["== 3. 约定的单点中断情景（同一中断下所有服务共用同一份排班与占用） =="]
    baseline, impacts = scenarios.factor_impacts(data)
    lines.append("正常排班：")
    for service_id in sorted(baseline.outcomes):
        outcome = baseline.outcomes[service_id]
        if outcome.status == scenarios.MET:
            hops = "+".join(edge.id for edge in outcome.route)
            lines.append(
                f"  {service_id} 经 {hops}，{fmt_time(outcome.completion)} 完成"
            )
        else:
            lines.append(f"  {service_id} {outcome.reason}")
    lines.append("单点中断（按最值得消除的共同断点排序）：")
    reserved = sorted(
        service.id for service in data.services.values() if service.reserve
    )
    for factor, _reserved, _total, lost in scenarios.rank_breakpoints(data, impacts):
        detail = "、".join(
            f"{service_id}（{'保留承诺' if data.services[service_id].reserve else '未承诺'}"
            f"{'，结构性' if _structural(factor, data.services[service_id]) else ''}）"
            for service_id in lost
        )
        kept = [service_id for service_id in reserved if service_id not in lost]
        lines.append(
            f"  {factor.label} 失效 → 失去 {detail or '无'}"
            f"；仍可兑现的保留承诺：{'、'.join(kept) or '无'}"
        )
    return lines


def _structural(factor: scenarios.Factor, service) -> bool:
    """源头或目的岗位本身的失效属结构性，不是可消除的共同断点。"""

    return factor.kind == "post" and factor.name in (service.source, service.target)


def section_evidence(data: Data) -> list[str]:
    lines = ["== 4. 演练证据分级（已知 / 可能 / 未知 / 已知失败） =="]
    for link in data.links.values():
        parts = []
        for shift in ("day", "night"):
            for required in (frozenset({"普通"}), frozenset({"需保密"})):
                if not required <= link.permission:
                    continue
                ev = evidence.link_evidence(data.drills, link.id, shift, required)
                if ev.status == evidence.UNKNOWN:
                    continue
                label = "夜班" if shift == "night" else "白班"
                perm = "/".join(sorted(required))
                extra = ""
                if ev.status == evidence.POSSIBLE and ev.interval:
                    extra = f"（可能确认 {fmt_time(ev.interval[0])}-{fmt_time(ev.interval[1])}）"
                parts.append(f"{label}{perm}:{ev.label}{extra}[{ev.source or '-'}]")
        lines.append(f"  {link.id}（{link.channel}）：" + "；".join(parts or ["无任何演练记录"]))
    lines.append("试拨链（同一 trial 为同一消息）：")
    for trial, rows in sorted(evidence.trial_chains(data.drills).items()):
        results = "、".join(f"{row.link}:{row.result}" for row in rows)
        ok = all(row.result == "confirmed" for row in rows)
        lines.append(f"  {trial}（{rows[0].shift}）{results} → {'整链确认' if ok else '未整链确认'}")
    return lines


def section_desk(data: Data) -> list[str]:
    lines = ["== 5. 真实换班事件与责任归属（按值班台可见时间推进） =="]
    result = desk.Desk(data).run()
    lines.extend(f"  {entry}" for entry in result.log)
    lines.append("换班后各承诺去向：")
    for service_id in sorted(result.states):
        state = result.states[service_id]
        if not state.history:
            continue  # 无任何签收或通知的承诺不在换班台帐上
        owner = state.owner or "无人签收"
        chain = f"，经历：{' → '.join(state.reasons)}" if state.reasons else ""
        lines.append(
            f"  {service_id}：归属 {owner}，{desk.STATUS_LABELS[state.status]}{chain}"
        )
    return lines


def section_options(data: Data) -> list[str]:
    lines = ["== 6. 新增联络安排比较 =="]
    comparison = options.compare_options(data)
    for conflict in comparison.conflicts:
        lines.append(f"  互斥：{conflict.first} 与 {conflict.second} —— {conflict.reason}")
    for cost in comparison.costs:
        lines.append(f"  代价：{cost.option} —— {cost.reason}")
    lines.append("各组合效果（仅列有变化的）：")
    for adopted in comparison.by_set:
        if not adopted:
            continue
        eliminated = comparison.eliminated(adopted)
        if not eliminated:
            lines.append(f"  采纳 {'+'.join(sorted(adopted))}：未消除任何共同断点")
            continue
        lines.append(f"  采纳 {'+'.join(sorted(adopted))}：")
        for service_id, points in sorted(eliminated.items()):
            lines.append(f"    {service_id} 消除共同断点：{'、'.join(sorted(points))}")
    best = max(
        comparison.by_set,
        key=lambda adopted: (
            sum(len(v) for v in comparison.eliminated(adopted).values()),
            -len(comparison.not_promisable(adopted)),
        ),
    )
    remaining = comparison.not_promisable(best)
    lines.append(
        f"建议组合 {'+'.join(sorted(best)) or '（不采纳）'}；"
        f"仍不宜提前答应：{'、'.join(remaining) if remaining else '无'}"
    )
    return lines


def section_review(data: Data) -> list[str]:
    lines = ["== 7. 复盘（按班次） =="]
    result = desk.Desk(data).run()
    for shift, label in (("day", "白班"), ("night", "夜班")):
        lines.append(f"{label}演练窗口：")
        signed = [
            r for r in data.receipts
            if r.kind == "signed" and shift_of(r.received) == shift
        ]
        for receipt in signed:
            lines.append(
                f"  当时承认的联络线：{receipt.service} 由 {receipt.owner} 经 {receipt.link}"
                f"（{fmt_time(receipt.received)} 签收）"
            )
        downs = [
            r for r in data.receipts
            if r.kind == "down" and shift_of(r.received) == shift
        ]
        for receipt in downs:
            restored = next(
                (r for r in data.receipts if r.kind == "restored" and r.prior == receipt.id),
                None,
            )
            until = f"，{fmt_time(restored.effective)} 恢复" if restored else "，未记录恢复"
            lines.append(
                f"  查实的中断：{receipt.channel} {fmt_time(receipt.effective)} 起"
                f"（{fmt_time(receipt.received)} 上台）{until}"
            )
        affected = [
            state for state in result.states.values()
            if state.owner is not None and state.history
            and any(shift_of(r.received) == shift for r in signed if r.service == state.service)
        ]
        for state in affected:
            chain = f"，经历 {' → '.join(state.reasons)}" if state.reasons else ""
            lines.append(
                f"  承诺去向：{state.service} → 归属 {state.owner}，"
                f"{desk.STATUS_LABELS[state.status]}{chain}"
            )
    return lines


def build_report(data: Data) -> str:
    sections = [
        section_routes(data),
        section_independence(data),
        section_scenarios(data),
        section_evidence(data),
        section_desk(data),
        section_options(data),
        section_review(data),
    ]
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    fixtures = Path(args[0]) if args else Path(__file__).resolve().parents[1] / "fixtures"
    print(build_report(load(fixtures)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
