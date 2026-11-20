"""真实换班：以本地签收、撤回和例外通知推进。

处理规则：

- 事件按值班台可见时间（received）顺序推进；物理通断按 effective 认定。
- signed：岗位签收服务，记下责任归属与计划用的边。
- 通道 down：计划用边受影响的服务明确标为"待恢复"，不悄悄推给另一条
  也已失效的路。
- handoff_proposed：备用接手提议；提议期间占用该人员的候命时段，阻止
  同一候命时段再划给别的链。备用接手尚未确认前，原归属不变。
- handoff_ack：确认到达时提议仍然有效才完成交接；提议已撤回的迟到
  确认不成立。
- handoff_withdrawn：撤回提议，释放候命时段，服务保持原归属。
- 通道 restored：不再依赖断点的服务放回原路线；赶不上截止的继续留在
  人工联络清单。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import KNOWN, LABELS, link_evidence
from .model import Data, Receipt, fmt_time
from .routes import shift_of

# 服务状态
OK = "ok"  # 按约推进
PENDING = "pending_recovery"  # 原路线失联，待恢复
MANUAL = "manual"  # 留在人工联络清单

STATUS_LABELS = {OK: "按约推进", PENDING: "待恢复", MANUAL: "人工联络清单"}

# 失去备援的原因分类
REASON_COMMON_SOURCE = "同源失效"
REASON_DEADLINE = "无法赶上约定时间"
REASON_EVIDENCE = "证据尚待回交"


@dataclass
class ServiceState:
    service: str
    owner: str | None = None
    planned_link: str | None = None
    status: str = OK
    reason: str | None = None
    reasons: list[str] = field(default_factory=list)  # 原因链，按发生顺序
    history: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.history.append(text)

    def flag(self, reason: str) -> None:
        self.reason = reason
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass
class Proposal:
    id: str
    service: str
    owner: str
    link: str
    proposed_at: int
    open: bool = True
    resolution: str | None = None


@dataclass
class DeskResult:
    states: dict[str, ServiceState]
    proposals: dict[str, Proposal]
    log: list[str]
    outages: list[Receipt]


class Desk:
    def __init__(self, data: Data):
        self.data = data
        self.states = {service_id: ServiceState(service_id) for service_id in data.services}
        self.proposals: dict[str, Proposal] = {}
        self.log: list[str] = []
        self.outages: list[Receipt] = []
        # 候命时段占用：人员 -> [(start, end, proposal_id)]
        self.standby: dict[str, list[tuple[int, int, str]]] = {}

    def run(self) -> DeskResult:
        for receipt in sorted(
            self.data.receipts, key=lambda r: (r.received, r.effective, r.id)
        ):
            handler = getattr(self, f"_on_{receipt.kind}", None)
            if handler is None:
                raise ValueError(f"未知回执类型: {receipt.kind}")
            handler(receipt)
        return DeskResult(
            states=self.states,
            proposals=self.proposals,
            log=self.log,
            outages=self.outages,
        )

    # --- 工具 ---

    def _say(self, receipt: Receipt, text: str) -> None:
        self.log.append(f"{fmt_time(receipt.received)} {receipt.id} {text}")

    def _state(self, service_id: str) -> ServiceState:
        return self.states[service_id]

    def _standby_blocked(self, person: str, start: int) -> str | None:
        """同一候命时段是否已划给别的链；返回占用它的提议 id。"""

        for busy_start, busy_end, proposal_id in self.standby.get(person, ()):
            if busy_start <= start < busy_end:
                return proposal_id
        return None

    # --- 各类回执 ---

    def _on_signed(self, receipt: Receipt) -> None:
        state = self._state(receipt.service)
        state.owner = receipt.owner
        state.planned_link = receipt.link
        state.status = OK
        state.reason = None
        state.note(f"{fmt_time(receipt.effective)} {receipt.owner} 签收，计划经 {receipt.link}")
        self._say(receipt, f"{receipt.service} 由 {receipt.owner} 签收（计划经 {receipt.link}）")

    def _on_down(self, receipt: Receipt) -> None:
        self.outages.append(receipt)
        self._say(
            receipt,
            f"通道 {receipt.channel} 中断（生效 {fmt_time(receipt.effective)}）",
        )
        for state in self.states.values():
            if state.owner is None or state.planned_link is None:
                continue
            link = self.data.links[state.planned_link]
            if link.channel != receipt.channel:
                continue
            state.status = PENDING
            state.flag(REASON_COMMON_SOURCE)
            state.note(
                f"{fmt_time(receipt.received)} 原路线经 {link.id} 失联，标为待恢复，"
                "不悄然改派其它已失效路线"
            )
            self._say(receipt, f"{state.service} 原路线失联，标为待恢复")

    def _on_restored(self, receipt: Receipt) -> None:
        self._say(
            receipt,
            f"通道 {receipt.channel} 恢复（生效 {fmt_time(receipt.effective)}）",
        )
        for state in self.states.values():
            if state.status != PENDING or state.planned_link is None:
                continue
            link = self.data.links[state.planned_link]
            if link.channel != receipt.channel:
                continue
            service = self.data.services[state.service]
            # 恢复生效后即可重发；完成须严格早于截止。
            completion = receipt.effective + link.minutes
            if completion < service.deadline:
                state.status = OK
                state.reason = None
                state.note(
                    f"{fmt_time(receipt.received)} 路线恢复，不再依赖断点，放回原归属"
                )
                self._say(receipt, f"{state.service} 不再依赖断点，放回原路线")
            else:
                state.status = MANUAL
                state.flag(REASON_DEADLINE)
                state.note(
                    f"{fmt_time(receipt.received)} 恢复后最早 {fmt_time(completion)} 完成，"
                    f"已晚于截止 {fmt_time(service.deadline)}，留在人工联络清单"
                )
                self._say(
                    receipt,
                    f"{state.service} 恢复后仍赶不上 {fmt_time(service.deadline)}，"
                    "留在人工联络清单",
                )

    def _on_handoff_proposed(self, receipt: Receipt) -> None:
        state = self._state(receipt.service)
        person = self.data.person_of(receipt.owner)
        blocker = self._standby_blocked(person, receipt.received)
        if blocker is not None:
            self._say(
                receipt,
                f"{receipt.service} 备用提议被拒绝：{person} 的候命时段已划给 {blocker}",
            )
            state.note(
                f"{fmt_time(receipt.received)} 备用提议 {receipt.id} 因候命时段冲突被拒"
            )
            return
        proposal = Proposal(
            id=receipt.id,
            service=receipt.service,
            owner=receipt.owner,
            link=receipt.link,
            proposed_at=receipt.received,
        )
        self.proposals[receipt.id] = proposal
        # 提议未决期间占用该人员候命时段（至当末），阻止再划给别的链。
        self.standby.setdefault(person, []).append(
            (receipt.received, 24 * 60, receipt.id)
        )
        state.note(
            f"{fmt_time(receipt.received)} {receipt.owner} 提议经 {receipt.link} 接手，"
            "未确认前原归属不变"
        )
        # 备用路线的演练证据不足时，当场标注证据尚待回交。
        service = self.data.services[receipt.service]
        ev = link_evidence(
            self.data.drills,
            receipt.link,
            shift_of(receipt.received),
            service.permission,
        )
        if ev.status != KNOWN:
            state.flag(REASON_EVIDENCE)
            state.note(
                f"{fmt_time(receipt.received)} 备用路线 {receipt.link} 证据为"
                f"「{LABELS[ev.status]}」，证据尚待回交"
            )
            self._say(
                receipt,
                f"{receipt.service} 备用路线 {receipt.link} 证据尚待回交"
                f"（{LABELS[ev.status]}）",
            )
        self._say(
            receipt,
            f"{receipt.service} 备用接手提议：{receipt.owner} 经 {receipt.link}"
            "（待确认，原归属不变）",
        )

    def _on_handoff_ack(self, receipt: Receipt) -> None:
        proposal = self.proposals.get(receipt.prior)
        state = self._state(receipt.service)
        if proposal is None or not proposal.open:
            self._say(
                receipt,
                f"{receipt.service} 的接手确认到达时提议已撤回，交接不成立，原归属不变",
            )
            state.note(
                f"{fmt_time(receipt.received)} 迟到确认 {receipt.id} 不成立，保持原归属"
            )
            return
        proposal.open = False
        proposal.resolution = "acked"
        self._release_standby(proposal)
        previous = state.owner
        state.owner = proposal.owner
        state.planned_link = proposal.link
        if state.status == PENDING:
            state.status = OK
            state.reason = None
        state.note(
            f"{fmt_time(receipt.received)} 接手确认成立，归属 {previous} -> {proposal.owner}"
        )
        self._say(
            receipt,
            f"{receipt.service} 交接确认成立：{previous} -> {proposal.owner}",
        )

    def _on_handoff_withdrawn(self, receipt: Receipt) -> None:
        proposal = self.proposals.get(receipt.prior)
        if proposal is None or not proposal.open:
            self._say(receipt, f"撤回指向的提议 {receipt.prior} 不在未决状态")
            return
        proposal.open = False
        proposal.resolution = "withdrawn"
        self._release_standby(proposal)
        state = self._state(receipt.service)
        state.note(
            f"{fmt_time(receipt.received)} 备用提议撤回，服务保持原归属 {state.owner}"
        )
        self._say(
            receipt,
            f"{receipt.service} 备用提议撤回，保持原归属 {state.owner}",
        )

    def _release_standby(self, proposal: Proposal) -> None:
        person = self.data.person_of(proposal.owner)
        self.standby[person] = [
            (start, end, proposal_id)
            for start, end, proposal_id in self.standby.get(person, ())
            if proposal_id != proposal.id
        ]
