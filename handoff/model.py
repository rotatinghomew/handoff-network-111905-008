"""源记录模型与装载。

均为合成运营记录。时间用同一天当地时钟的分钟数表示；时段左闭右开。
空值没有声明事实，不等于零。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


def parse_time(text: str) -> int:
    """"HH:MM" -> 当天分钟数。"""
    hh, mm = text.strip().split(":")
    return int(hh) * 60 + int(mm)


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_permission(text: str) -> frozenset[str]:
    return frozenset(part.strip() for part in text.split(";") if part.strip())


@dataclass(frozen=True)
class Duty:
    """值班岗位。on_site 表示此岗已承诺到场，不可随意改作候命。"""

    id: str
    person: str
    start: int
    end: int
    permission: frozenset[str]
    on_site: bool


@dataclass(frozen=True)
class Link:
    """有向联络段。minutes 为联络加确认共占的总分钟。

    channel 是共享故障因素；gateway 是还会共同失效的岗位。
    """

    id: str
    source: str
    target: str
    start: int
    end: int
    channel: str
    gateway: str | None
    minutes: int
    permission: frozenset[str]


@dataclass(frozen=True)
class Service:
    """服务承诺：source 发出，target 须在 deadline 前签收。

    reserve 表示已有答应的责任归属，不能被新安排占用。
    """

    id: str
    source: str
    target: str
    release: int
    deadline: int
    permission: frozenset[str]
    reserve: bool


@dataclass(frozen=True)
class Drill:
    """演练段证据。trial 标同一消息试拨链；区间是可能确认时间。"""

    id: str
    trial: str
    link: str
    shift: str  # day / night
    permission: frozenset[str]
    result: str  # confirmed / missing / failure
    start: int
    end: int


@dataclass(frozen=True)
class Option:
    """拟新增联络边或改为候命。resource 是试拨名额（每时段仅一份）或人员。"""

    id: str
    source: str
    target: str
    channel: str
    permission: frozenset[str]
    resource: str
    start: int
    end: int
    minutes: int
    replaces: str


@dataclass(frozen=True)
class Receipt:
    """真实换班或中断通知。received 为值班台可见时间。"""

    id: str
    service: str | None
    link: str | None
    channel: str | None
    kind: str
    effective: int
    received: int
    owner: str | None
    prior: str | None


@dataclass
class Data:
    duties: dict[str, Duty]
    links: dict[str, Link]
    services: dict[str, Service]
    drills: list[Drill]
    options: dict[str, Option]
    receipts: list[Receipt]

    @property
    def persons(self) -> set[str]:
        return {duty.person for duty in self.duties.values()}

    def person_of(self, post: str) -> str:
        return self.duties[post].person


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def load(fixtures: str | Path) -> Data:
    root = Path(fixtures)

    duties = {}
    for row in _rows(root / "duties.csv"):
        duty = Duty(
            id=row["id"],
            person=row["person"],
            start=parse_time(row["start"]),
            end=parse_time(row["end"]),
            permission=parse_permission(row["permission"]),
            on_site=row["on_site"].strip().lower() == "yes",
        )
        duties[duty.id] = duty

    links = {}
    for row in _rows(root / "links.csv"):
        link = Link(
            id=row["id"],
            source=row["source"],
            target=row["target"],
            start=parse_time(row["from"]),
            end=parse_time(row["to"]),
            channel=row["channel"],
            gateway=row["gateway"] or None,
            minutes=int(row["minutes"]),
            permission=parse_permission(row["permission"]),
        )
        links[link.id] = link

    services = {}
    for row in _rows(root / "services.csv"):
        service = Service(
            id=row["id"],
            source=row["source"],
            target=row["target"],
            release=parse_time(row["release"]),
            deadline=parse_time(row["deadline"]),
            permission=parse_permission(row["permission"]),
            reserve=row["reserve"].strip().lower() == "yes",
        )
        services[service.id] = service

    drills = [
        Drill(
            id=row["id"],
            trial=row["trial"],
            link=row["link"],
            shift=row["shift"],
            permission=parse_permission(row["permission"]),
            result=row["result"],
            start=parse_time(row["from"]),
            end=parse_time(row["to"]),
        )
        for row in _rows(root / "tests.csv")
    ]

    options = {}
    for row in _rows(root / "options.csv"):
        option = Option(
            id=row["id"],
            source=row["source"],
            target=row["target"],
            channel=row["channel"],
            permission=parse_permission(row["permission"]),
            resource=row["resource"],
            start=parse_time(row["start"]),
            end=parse_time(row["end"]),
            minutes=int(row["minutes"]),
            replaces=row["replaces"],
        )
        options[option.id] = option

    receipts = [
        Receipt(
            id=row["id"],
            service=row["service"] or None,
            link=row["link"] or None,
            channel=row["channel"] or None,
            kind=row["kind"],
            effective=parse_time(row["effective"]),
            received=parse_time(row["received"]),
            owner=row["owner"] or None,
            prior=row["prior"] or None,
        )
        for row in _rows(root / "receipts.csv")
    ]

    return Data(
        duties=duties,
        links=links,
        services=services,
        drills=drills,
        options=options,
        receipts=receipts,
    )
