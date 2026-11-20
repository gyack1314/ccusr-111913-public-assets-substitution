"""备选方案生成与比较。

备选方案之间可以共享设备（它们只是候选）；只有被接受的方案才形成
实际预留。每个方案内部同一资产只出现一次。方案生成是确定性的：
同样的资料与状态总是得到同样的备选集合，便于复核。
"""
from __future__ import annotations

import itertools

from . import rules, savings

CANDIDATES_PER_KIND = 8


def _asset_ids(assignment: dict) -> set:
    return {aid for ids in assignment["items"].values() for aid in ids}


def _covered_units(assignment: dict) -> int:
    return sum(assignment["scope"].values())


def enumerate_assignments(
    dataset,
    need,
    remaining: dict,
    asset_flags: dict,
    reservations: list,
    as_of,
    existing: dict | None = None,
) -> list:
    """为一项需求枚举可行指派。

    remaining: {组件: 尚需件数}。不拆分需求只产出整组指派；可拆分需求
    还会产出按组件划分的部分指派。只保留满足供给状态、检测证据、用途
    兼容与期限要求的组合。existing 为该需求已在效预留的
    {组件: [Asset]}：分期补足时，新组合必须与已在用资产一并校验兼容。
    """
    index = rules.build_compat_index(dataset.compats, as_of)
    existing = existing or {}
    kinds = sorted(k for k, q in remaining.items() if q > 0)
    if not kinds:
        return []

    candidates: dict = {}
    for kind in kinds:
        pool = []
        for asset in dataset.assets_of_kind(kind):
            ok, _ = rules.committable(dataset, asset, asset_flags)
            if not ok:
                continue
            ready = rules.effective_ready_day(dataset, asset, need, reservations)
            if ready > need.deadline:
                continue
            necessary_repair = asset.repair_cents if rules.repair_required(asset) else 0
            pool.append((asset, ready, necessary_repair))
        pool.sort(key=lambda item: (item[1], item[2], item[0].id))
        candidates[kind] = pool[:CANDIDATES_PER_KIND]

    if any(not candidates[kind] for kind in kinds):
        return []

    if need.splittable:
        subsets = []
        for size in range(1, len(kinds) + 1):
            subsets.extend(itertools.combinations(kinds, size))
    else:
        subsets = [tuple(kinds)]

    assignments = []
    for subset in subsets:
        per_kind_combos = []
        for kind in subset:
            qty = remaining[kind]
            combos = list(itertools.combinations(candidates[kind], qty))
            if not combos:
                break
            per_kind_combos.append((kind, combos))
        else:
            for pick in itertools.product(*(c for _, c in per_kind_combos)):
                items = {}
                chosen = {}
                for (kind, _), combo in zip(per_kind_combos, pick):
                    assets = [entry[0] for entry in combo]
                    items[kind] = [a.id for a in assets]
                    chosen[kind] = assets
                # 与已在用资产合并校验套件兼容
                merged = {
                    kind: list(existing.get(kind, [])) + list(chosen.get(kind, []))
                    for kind in set(existing) | set(chosen)
                }
                ok, _, evidence = rules.suite_check(index, need.usage, merged)
                if not ok:
                    continue
                arrival_by_kind = {}
                repair = 0
                for kind, combo in zip((k for k, _ in per_kind_combos), pick):
                    arrival_by_kind[kind] = max(entry[1] for entry in combo)
                    repair += sum(entry[2] for entry in combo)
                arrival = max(arrival_by_kind.values())
                assignments.append(
                    {
                        "need": need.id,
                        "items": items,
                        "scope": {k: remaining[k] for k in subset},
                        "arrival": arrival.isoformat(),
                        "arrival_by_kind": {
                            k: d.isoformat() for k, d in arrival_by_kind.items()
                        },
                        "repair_cents": repair,
                        "evidence": evidence,
                    }
                )

    assignments.sort(
        key=lambda a: (
            -_covered_units(a),
            a["arrival"],
            a["repair_cents"],
            sorted(_asset_ids(a)),
        )
    )
    # 不截断：靠后需求在拼装时需要占用不同资产的可行备选
    return assignments


def _simple_net(dataset, need, assignment: dict) -> int:
    """方案内排序用的粗略净额：不考虑共享搬运分摊。"""
    proc = dataset.procurement_of(need.id)
    if proc is None or not proc.approved:
        gross = 0
        delivery = 0
    else:
        fraction, _ = savings.recognized_fraction(need, assignment["scope"])
        gross = savings.gross_cents(proc, fraction)
        delivery = proc.delivery_cents if fraction > 0 else 0
    return gross - assignment["repair_cents"] - delivery


STRATEGIES = {
    "priority-first": "按优先级与期限安排，指派取最早到位",
    "savings-first": "按优先级与期限安排，指派取粗略净节支最高",
    "approved-first": "已获准新购的需求优先安排，指派取最早到位",
}


def _need_order(dataset, need, strategy: str):
    if strategy == "approved-first":
        proc = dataset.procurement_of(need.id)
        not_approved = 0 if (proc and proc.approved) else 1
        return (not_approved, need.priority, need.deadline, need.id)
    return (need.priority, need.deadline, need.id)


def _assign_key(dataset, need, assignment: dict, strategy: str):
    ids = sorted(_asset_ids(assignment))
    if strategy == "savings-first":
        return (
            -_covered_units(assignment),
            -_simple_net(dataset, need, assignment),
            assignment["arrival"],
            ids,
        )
    return (
        -_covered_units(assignment),
        assignment["arrival"],
        assignment["repair_cents"],
        ids,
    )


def assemble_plan(
    dataset, needs: list, assignments_by_need: dict, strategy: str
) -> dict:
    """按策略把各需求指派拼成一个互不冲突的备选方案。"""
    ordered = sorted(needs, key=lambda n: _need_order(dataset, n, strategy))
    used: set = set()
    chosen: list = []
    uncovered: list = []
    for need in ordered:
        options = [
            a
            for a in assignments_by_need.get(need.id, [])
            if not (_asset_ids(a) & used)
        ]
        if not options:
            if assignments_by_need.get(need.id):
                reason = "可行资产已被本方案中更先安排的需求占用"
            else:
                reason = "无满足供给状态、检测证据、用途兼容与期限要求的组合"
            uncovered.append({"need": need.id, "reason": reason})
            continue
        pick = min(options, key=lambda a: _assign_key(dataset, need, a, strategy))
        chosen.append(pick)
        used |= _asset_ids(pick)
    return {"strategy": strategy, "assignments": chosen, "uncovered": uncovered}


def estimate_plan(
    dataset, plan: dict, current_recognized: dict, existing_scope: dict | None = None
) -> dict:
    """估算方案被接受后的认定结果（按接受时的口径：与已在效预留合并）。

    current_recognized: {共享搬运组: 当前已认定需求id集合}。共享搬运按
    “现有认定 ∪ 本方案认定”预估分摊，与接受时的口径一致。
    existing_scope: {需求id: (已在效范围, 已在效资产)}，用于分期补足时
    按合并后的总范围估算。
    """
    existing_scope = existing_scope or {}
    per_need: dict = {}
    plan_recognized: dict = {}
    for assignment in plan["assignments"]:
        need = dataset.needs[assignment["need"]]
        proc = dataset.procurement_of(need.id)
        base_scope, base_assets = existing_scope.get(need.id, ({}, []))
        combined_scope = dict(base_scope)
        for kind, qty in assignment["scope"].items():
            combined_scope[kind] = combined_scope.get(kind, 0) + qty
        fraction, basis = savings.recognized_fraction(need, combined_scope)
        repair = assignment["repair_cents"] + sum(
            dataset.assets[aid].repair_cents
            for aid in base_assets
            if rules.repair_required(dataset.assets[aid])
        )
        entry = {
            "fraction_num": fraction.numerator,
            "fraction_den": fraction.denominator,
            "basis": basis,
            "approved": bool(proc and proc.approved),
            "gross_cents": 0,
            "repair_cents": repair,
            "delivery_cents": 0,
            "trip_share_cents": 0,
            "net_cents": 0,
        }
        if proc and proc.approved and fraction > 0:
            entry["gross_cents"] = savings.gross_cents(proc, fraction)
            entry["delivery_cents"] = proc.delivery_cents
            if proc.shared_trip:
                plan_recognized.setdefault(proc.shared_trip, set()).add(need.id)
        per_need[need.id] = entry

    groups = set(plan_recognized) | set(current_recognized)
    for group in groups:
        recognized = set(current_recognized.get(group, set()))
        recognized |= plan_recognized.get(group, set())
        trip_cents = _group_trip_cents(dataset, group)
        if not recognized or trip_cents is None:
            continue
        shares = savings.allocate_shares(recognized, trip_cents)
        for nid, share in shares.items():
            if nid in per_need:
                per_need[nid]["trip_share_cents"] = share

    for nid, entry in per_need.items():
        if entry["approved"] and (entry["fraction_num"] > 0):
            entry["net_cents"] = (
                entry["gross_cents"]
                - entry["repair_cents"]
                - entry["delivery_cents"]
                - entry["trip_share_cents"]
            )

    full = 0
    partial = 0
    for assignment in plan["assignments"]:
        need = dataset.needs[assignment["need"]]
        base_scope, _ = existing_scope.get(need.id, ({}, []))
        combined = dict(base_scope)
        for kind, qty in assignment["scope"].items():
            combined[kind] = combined.get(kind, 0) + qty
        if all(combined.get(k, 0) >= q for k, q in need.quantities.items()):
            full += 1
        else:
            partial += 1
    return {
        "per_need": per_need,
        "recognized_net_cents": sum(e["net_cents"] for e in per_need.values()),
        "covered_full": full,
        "covered_partial": partial,
    }


def _group_trip_cents(dataset, group: str) -> int | None:
    for proc in dataset.procurement_by_need.values():
        if proc.shared_trip == group:
            return proc.trip_cents
    return None
