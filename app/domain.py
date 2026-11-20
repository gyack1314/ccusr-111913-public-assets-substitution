"""领域记录：把原始资料行解析为带类型的对象，并提供数据集视图。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


def parse_day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def parse_ts(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str
    model: str
    state: str
    available_day: dt.date
    condition_score: int | None
    repair_cents: int
    repair_days: int
    location: str


@dataclass(frozen=True)
class Need:
    id: str
    department: str
    usage: str
    quantities: dict
    splittable: bool
    start_day: dt.date
    end_day: dt.date
    priority: int
    deadline: dt.date
    procurement_id: str

    @property
    def total_units(self) -> int:
        return sum(self.quantities.values())


@dataclass(frozen=True)
class Compat:
    id: str
    usage: str
    left_model: str
    right_model: str
    compatible: bool
    known_at: dt.datetime


@dataclass(frozen=True)
class Occupancy:
    id: str
    asset: str
    department: str
    until_day: dt.date
    return_check_days: int
    transport_days: int


@dataclass(frozen=True)
class Procurement:
    id: str
    need: str
    approved: bool
    comparable_cents: int
    delivery_cents: int
    shared_trip: str | None
    trip_cents: int | None


@dataclass(frozen=True)
class Handover:
    id: str
    asset: str | None
    known_at: dt.datetime
    kind: str
    need: str | None
    note: str


@dataclass
class Dataset:
    """原始资料的内存视图；只读，不含任何运行时决定。"""

    version: str
    assets: dict  # id -> Asset
    needs: dict  # id -> Need
    compats: list  # [Compat]
    occupancies: dict  # asset id -> [Occupancy]
    procurement_by_need: dict  # need id -> Procurement
    handovers: list  # [Handover]

    def assets_of_kind(self, kind: str) -> list:
        return sorted(
            (a for a in self.assets.values() if a.kind == kind),
            key=lambda a: a.id,
        )

    def procurement_of(self, need_id: str) -> Procurement | None:
        return self.procurement_by_need.get(need_id)

    def occupancy_of(self, asset_id: str) -> list:
        return self.occupancies.get(asset_id, [])


def build_dataset(tables: dict, version: str) -> Dataset:
    assets = {}
    for row in tables["assets"]:
        asset = Asset(
            id=row["id"],
            kind=row["kind"],
            model=row["model"],
            state=row["state"],
            available_day=parse_day(row["available_day"]),
            condition_score=row["condition_score"],
            repair_cents=row["repair_cents"],
            repair_days=row["repair_days"],
            location=row["location"],
        )
        assets[asset.id] = asset

    needs = {}
    for row in tables["needs"]:
        need = Need(
            id=row["id"],
            department=row["department"],
            usage=row["usage"],
            quantities=dict(row["quantities"]),
            splittable=bool(row["splittable"]),
            start_day=parse_day(row["start_day"]),
            end_day=parse_day(row["end_day"]),
            priority=row["priority"],
            deadline=parse_day(row["deadline"]),
            procurement_id=row["procurement_id"],
        )
        needs[need.id] = need

    compats = [
        Compat(
            id=row["id"],
            usage=row["usage"],
            left_model=row["left_model"],
            right_model=row["right_model"],
            compatible=bool(row["compatible"]),
            known_at=parse_ts(row["known_at"]),
        )
        for row in tables["compatibility"]
    ]

    occupancies: dict = {}
    for row in tables["occupancies"]:
        occ = Occupancy(
            id=row["id"],
            asset=row["asset"],
            department=row["department"],
            until_day=parse_day(row["until_day"]),
            return_check_days=row["return_check_days"],
            transport_days=row["transport_days"],
        )
        occupancies.setdefault(occ.asset, []).append(occ)

    procurement_by_need = {}
    for row in tables["procurement"]:
        proc = Procurement(
            id=row["id"],
            need=row["need"],
            approved=bool(row["approved"]),
            comparable_cents=row["comparable_cents"],
            delivery_cents=row["delivery_cents"],
            shared_trip=row["shared_trip"],
            trip_cents=row["trip_cents"],
        )
        procurement_by_need[proc.need] = proc

    handovers = [
        Handover(
            id=row["id"],
            asset=row["asset"],
            known_at=parse_ts(row["known_at"]),
            kind=row["kind"],
            need=row["need"],
            note=row["note"],
        )
        for row in tables["handover"]
    ]

    return Dataset(
        version=version,
        assets=assets,
        needs=needs,
        compats=compats,
        occupancies=occupancies,
        procurement_by_need=procurement_by_need,
        handovers=handovers,
    )
