"""可替代判断：供给状态、检测证据、兼容关系与到位日期。

口径约定（与 docs/input-contract.md 一致）：
- 闲置 idle / 已检测 tested / 可修复 repairable 是三种可供给状态；
  待检测 quarantine 不可承诺；检测得分未知（condition_score 为空）同样
  不能按全部可用来承诺。
- 只有可修复状态的资产计入必要维修（维修费与维修日数）；其余状态的
  维修报价仅作资料，不计入到位日或节支扣除。
- 占用含 until_day 当天，随后 return_check_days 与 transport_days 对下一
  需求均不可用；短期借用结束不等于马上可承诺给下一家。
- 兼容关系按用途分别成立：只有该用途下已确认 compatible=true 的型号
  配对可用；已确认不兼容与未列配对（未知）都不能用于承诺。
"""
from __future__ import annotations

import datetime as dt

COMMITTABLE_STATES = {"idle", "tested", "repairable"}
STATE_LABELS = {
    "idle": "闲置",
    "tested": "已检测",
    "repairable": "可修复",
    "quarantine": "待检测",
}

# 交接事件对资产当前结论的标记
FLAG_LABELS = {
    "inspection_failed": "交接后检测判定不适用",
    "returned_pending_check": "退回后待重新检测",
}

# 资料未给出返还检测/运输日数的资产，采用资料中占用记录的通行值，
# 并在预留记录中写明，便于复核。
DEFAULT_RETURN_CHECK_DAYS = 2
DEFAULT_TRANSPORT_DAYS = 1


def repair_required(asset) -> bool:
    """只有可修复状态的资产必须先维修再交付。"""
    return asset.state == "repairable"


def buffer_days(dataset, asset_id: str) -> int:
    """一次借用结束后、可再承诺之前必须留出的检测与运输日数。"""
    occs = dataset.occupancy_of(asset_id)
    if occs:
        occ = occs[0]
        return occ.return_check_days + occ.transport_days
    return DEFAULT_RETURN_CHECK_DAYS + DEFAULT_TRANSPORT_DAYS


def release_day(dataset, asset) -> dt.date:
    """资产对下一需求的最早释放日（不含维修）。"""
    day = asset.available_day
    for occ in dataset.occupancy_of(asset.id):
        free = occ.until_day + dt.timedelta(
            days=occ.return_check_days + occ.transport_days + 1
        )
        if free > day:
            day = free
    return day


def ready_day(dataset, asset) -> dt.date:
    """含必要维修的可交付日。"""
    day = release_day(dataset, asset)
    if repair_required(asset):
        day += dt.timedelta(days=asset.repair_days)
    return day


def committable(dataset, asset, asset_flags: dict) -> tuple:
    """当前事实下资产能否承诺。asset_flags 来自已登记的交接事件。"""
    flag = asset_flags.get(asset.id)
    if flag is not None:
        return False, FLAG_LABELS.get(flag, flag)
    if asset.state not in COMMITTABLE_STATES:
        label = STATE_LABELS.get(asset.state, asset.state)
        return False, f"供给状态为{label}，不可承诺"
    if asset.condition_score is None:
        return False, "检测结果未知，不能按可用来承诺"
    return True, "可承诺"


def build_compat_index(compats, as_of: dt.date | None) -> dict:
    """{(usage, model_a, model_b): (compatible, evidence_id)}。

    只纳入 as_of 当日及之前已知的声明；同一配对取最新到达的结论。
    索引按无序配对存储：资料中的配对声明对两个方向同样适用。
    """
    index: dict = {}
    for comp in compats:
        if as_of is not None and comp.known_at.date() > as_of:
            continue
        key = (comp.usage,) + tuple(sorted((comp.left_model, comp.right_model)))
        existing = index.get(key)
        if existing is None or comp.known_at >= existing[2]:
            index[key] = (comp.compatible, comp.id, comp.known_at)
    return index


def pair_status(index: dict, usage: str, model_a: str, model_b: str) -> tuple:
    """返回 (confirmed/denied/unknown, 证据声明id或None)。"""
    key = (usage,) + tuple(sorted((model_a, model_b)))
    found = index.get(key)
    if found is None:
        return "unknown", None
    compatible, evidence_id, _ = found
    return ("confirmed" if compatible else "denied"), evidence_id


def suite_check(index: dict, usage: str, chosen: dict) -> tuple:
    """检查一组选定资产在该用途下的跨类别两两型号兼容。

    chosen: {组件类别: [Asset]}。返回 (是否可行, 问题列表, 证据id列表)。
    同类别多件之间不需要配对（两台主机互不连接）。
    """
    problems: list = []
    evidence: list = []
    kinds = sorted(chosen)
    for i, kind_a in enumerate(kinds):
        for kind_b in kinds[i + 1 :]:
            for asset_a in chosen[kind_a]:
                for asset_b in chosen[kind_b]:
                    status, ev = pair_status(
                        index, usage, asset_a.model, asset_b.model
                    )
                    pair = f"{asset_a.id}({asset_a.model})×{asset_b.id}({asset_b.model})"
                    if status == "confirmed":
                        evidence.append(ev)
                    elif status == "denied":
                        problems.append(f"{pair} 已确认不兼容（依据 {ev}）")
                    else:
                        problems.append(f"{pair} 兼容性未知，不能默认兼容")
    return (not problems, problems, sorted(set(evidence)))


def effective_ready_day(dataset, asset, need, reservations: list) -> dt.date:
    """考虑在效预留后的实际可交付日。

    预留窗口 [from_day, until_release] 含交接缓冲；本需求若与在效预留
    冲突，则顺延到该预留释放之后。返回日期可能晚于需求期限，由调用方
    判定不可行。
    """
    day = ready_day(dataset, asset)
    window_end = need.end_day + dt.timedelta(days=buffer_days(dataset, asset.id))
    for res in sorted(
        (r for r in reservations if r["asset"] == asset.id and r["status"] == "active"),
        key=lambda r: (r["until_release"], r["id"]),
    ):
        start = dt.date.fromisoformat(res["from_day"])
        until = dt.date.fromisoformat(res["until_release"])
        if start <= window_end and day <= until:
            day = until + dt.timedelta(days=1)
    return day
