"""运行时状态：.local/state.json 的读写。

状态只由业务操作（app.operations）改写，包含：资料版本、备选方案、
在效预留、节支说明版本、共享搬运分摊版本、已登记交接事件及其影响。
"""
from __future__ import annotations

import json
import pathlib

SCHEMA = 1


def new_state(data_version: str, business_day: str) -> dict:
    return {
        "schema": SCHEMA,
        "data_version": data_version,
        "business_day": business_day,
        "plans": {},
        "reservations": [],
        "statements": [],
        "allocations": [],
        "handovers": [],
        "asset_flags": {},
        "need_overrides": {},
        "counters": {
            "plan": 0,
            "reservation": 0,
            "statement": 0,
            "allocation": 0,
            "handover": 0,
        },
    }


def load_state(path) -> dict:
    path = pathlib.Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"状态文件不存在：{path}。请先运行 python3 -m app.cli import"
        )
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != SCHEMA:
        raise ValueError("状态文件版本不匹配，请重置后重新导入")
    return state


def save_state(path, state: dict) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def state_exists(path) -> bool:
    return pathlib.Path(path).exists()


def next_id(state: dict, counter: str, prefix: str) -> str:
    state["counters"][counter] += 1
    return f"{prefix}-{state['counters'][counter]:04d}"


def active_reservations(state: dict) -> list:
    return [r for r in state["reservations"] if r["status"] == "active"]


def active_statement(state: dict, need_id: str) -> dict | None:
    for stmt in reversed(state["statements"]):
        if stmt["need"] == need_id and stmt["status"] == "active":
            return stmt
    return None


def active_allocation(state: dict, group: str) -> dict | None:
    for alloc in reversed(state["allocations"]):
        if alloc["group"] == group and alloc["status"] == "active":
            return alloc
    return None
