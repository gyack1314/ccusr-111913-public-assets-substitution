"""业务操作：导入、方案生成、接受预留、交接登记、状态与审计视图。

所有改写状态的操作都遵循：先核对当前可用条件，再落账；冲突明确列出，
不暗中删减套件内容。节支说明按版本保存，旧版本始终可复核。
"""
from __future__ import annotations

import datetime as dt
import pathlib

from support.inspect_data import load as load_tables

from . import domain, planning, rules, savings
from . import state as state_mod

HANDOVER_KINDS = {
    "inspection_failed": "关键设备检测不适用",
    "return_requested": "接收方退回（待重新检测）",
    "switched_to_new_purchase": "改用新购",
}


def load_dataset(root=None) -> domain.Dataset:
    manifest, tables = load_tables(root)
    return domain.build_dataset(tables, manifest["version"])


def _load_pair(state_path):
    state = state_mod.load_state(state_path)
    dataset = load_dataset()
    if state["data_version"] != dataset.version:
        raise ValueError(
            f"资料版本已变化（状态记录 {state['data_version']}，当前 {dataset.version}），"
            "请核对后重置状态再导入"
        )
    return state, dataset


# ---------------------------------------------------------------- 导入


def import_data(state_path, business_day: str | None = None) -> dict:
    """校验并导入本地资料，建立初始状态。business_day 缺省取资料中最晚日期。"""
    if state_mod.state_exists(state_path):
        raise ValueError("状态已存在；如需重新开始请先运行 reset")
    dataset = load_dataset()
    if business_day is None:
        candidates = [n.deadline for n in dataset.needs.values()]
        candidates += [c.known_at.date() for c in dataset.compats]
        candidates += [h.known_at.date() for h in dataset.handovers]
        day = max(candidates)
    else:
        day = dt.date.fromisoformat(business_day)
    state = state_mod.new_state(dataset.version, day.isoformat())
    state_mod.save_state(state_path, state)
    return {
        "data_version": dataset.version,
        "business_day": day.isoformat(),
        "needs": len(dataset.needs),
        "assets": len(dataset.assets),
    }


def reset(state_path) -> dict:
    path = pathlib.Path(state_path)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"removed": existed}


# ---------------------------------------------------------------- 视图辅助


def current_scope(state: dict, need_id: str) -> tuple:
    """当前在效预留构成的认定范围：({组件: 件数}, [资产id])。"""
    scope: dict = {}
    assets: list = []
    for res in state["reservations"]:
        if res["need"] == need_id and res["status"] == "active":
            scope[res["component"]] = scope.get(res["component"], 0) + 1
            assets.append(res["asset"])
    return scope, assets


def current_fraction(state: dict, dataset, need_id: str) -> tuple:
    scope, _ = current_scope(state, need_id)
    return savings.recognized_fraction(dataset.needs[need_id], scope)


def remaining_quantities(state: dict, dataset, need) -> dict:
    scope, _ = current_scope(state, need.id)
    return {
        kind: max(0, qty - scope.get(kind, 0))
        for kind, qty in need.quantities.items()
    }


def _recognized_by_group(state: dict, dataset) -> dict:
    recognized: dict = {}
    for nid, proc in dataset.procurement_by_need.items():
        if not proc.approved or not proc.shared_trip:
            continue
        fraction, _ = current_fraction(state, dataset, nid)
        if fraction > 0:
            recognized.setdefault(proc.shared_trip, set()).add(nid)
    return recognized


# ---------------------------------------------------------------- 方案生成


def _existing_by_need(state: dict, dataset) -> dict:
    """各需求当前在效预留的 {需求id: {组件: [Asset]}}，用于套件兼容合并校验。"""
    existing: dict = {}
    for res in state_mod.active_reservations(state):
        existing.setdefault(res["need"], {}).setdefault(res["component"], []).append(
            dataset.assets[res["asset"]]
        )
    return existing


def generate_plans(state_path, strategies: list | None = None) -> list:
    """生成备选方案（可共享设备），存入状态并返回方案列表。"""
    state, dataset = _load_pair(state_path)
    as_of = dt.date.fromisoformat(state["business_day"])
    flags = state["asset_flags"]
    reservations = state_mod.active_reservations(state)
    existing = _existing_by_need(state, dataset)

    targets = []
    remaining_by_need = {}
    for need in sorted(dataset.needs.values(), key=lambda n: n.id):
        if state["need_overrides"].get(need.id) == "closed_new_purchase":
            continue
        remaining = remaining_quantities(state, dataset, need)
        if sum(remaining.values()) == 0:
            continue
        targets.append(need)
        remaining_by_need[need.id] = remaining

    assignments_by_need = {
        need.id: planning.enumerate_assignments(
            dataset,
            need,
            remaining_by_need[need.id],
            flags,
            reservations,
            as_of,
            existing.get(need.id),
        )
        for need in targets
    }

    names = strategies or list(planning.STRATEGIES)
    recognized = _recognized_by_group(state, dataset)
    scope_for_estimate = {
        nid: current_scope(state, nid) for nid in (n.id for n in targets)
    }
    created = []
    for name in names:
        if name not in planning.STRATEGIES:
            raise ValueError(f"未知策略 {name}；可选：{sorted(planning.STRATEGIES)}")
        plan = planning.assemble_plan(dataset, targets, assignments_by_need, name)
        if not plan["assignments"]:
            continue
        plan["id"] = state_mod.next_id(state, "plan", "PLN")
        plan["created_day"] = state["business_day"]
        plan["status"] = "proposed"
        plan["strategy_note"] = planning.STRATEGIES[name]
        plan["estimate"] = planning.estimate_plan(
            dataset, plan, recognized, scope_for_estimate
        )
        plan["last_conflicts"] = []
        state["plans"][plan["id"]] = plan
        created.append(plan)
    state_mod.save_state(state_path, state)
    return created


# ---------------------------------------------------------------- 节支说明与分摊


def revise_statement(state: dict, dataset, need_id: str, reason: str) -> dict | None:
    """按当前在效预留重算并出具需求说明新版本；旧版本转为可复核历史。"""
    need = dataset.needs[need_id]
    proc = dataset.procurement_of(need_id)
    scope, assets = current_scope(state, need_id)
    fraction, basis = savings.recognized_fraction(need, scope)
    prev = state_mod.active_statement(state, need_id)
    if fraction == 0 and prev is None:
        return None
    alloc = (
        state_mod.active_allocation(state, proc.shared_trip)
        if proc and proc.shared_trip
        else None
    )
    share = alloc["shares"].get(need_id, 0) if alloc else 0
    gross = savings.gross_cents(proc, fraction) if proc and proc.approved else 0
    repair = sum(
        dataset.assets[aid].repair_cents
        for aid in assets
        if rules.repair_required(dataset.assets[aid])
    )
    delivery = proc.delivery_cents if (proc and fraction > 0) else 0
    net = gross - repair - delivery - share
    stmt = {
        "id": state_mod.next_id(state, "statement", "ST"),
        "need": need_id,
        "version": (prev["version"] + 1) if prev else 1,
        "issued_day": state["business_day"],
        "reason": reason,
        "status": "active",
        "supersedes": prev["id"] if prev else None,
        "scope": scope,
        "assets": sorted(assets),
        "fraction_num": fraction.numerator,
        "fraction_den": fraction.denominator,
        "gross_cents": gross,
        "repair_cents": repair,
        "delivery_cents": delivery,
        "trip_share_cents": share,
        "net_cents": net,
        "basis": {
            "fraction": basis,
            "repair": savings.REPAIR_BASIS,
            "delivery": savings.DELIVERY_BASIS if delivery else "未发生认定，不计交付安装费",
            "trip": alloc["basis"] if alloc else "无共享搬运分摊",
        },
    }
    if prev:
        prev["status"] = "superseded"
    state["statements"].append(stmt)
    return stmt


def refresh_group(state: dict, dataset, group: str, reason: str) -> set:
    """重算共享搬运组分摊；分摊变化的需求出具说明新版本。返回被修订需求。"""
    recognized = sorted(
        nid
        for nid, proc in dataset.procurement_by_need.items()
        if proc.shared_trip == group
        and proc.approved
        and current_fraction(state, dataset, nid)[0] > 0
    )
    trip_cents = planning._group_trip_cents(dataset, group) or 0
    shares = savings.allocate_shares(recognized, trip_cents) if recognized else {}
    current = state_mod.active_allocation(state, group)
    if current and current["shares"] == shares:
        return set()
    if current:
        current["status"] = "superseded"
    alloc = {
        "id": state_mod.next_id(state, "allocation", "ALC"),
        "group": group,
        "version": (current["version"] + 1) if current else 1,
        "issued_day": state["business_day"],
        "reason": reason,
        "trip_cents": trip_cents,
        "shares": shares,
        "basis": (
            savings.trip_basis_text(group, trip_cents, shares)
            if shares
            else "组内当前无认定需求，共享搬运费不计列"
        ),
        "status": "active",
    }
    state["allocations"].append(alloc)
    previous = current["shares"] if current else {}
    changed = {
        nid
        for nid in set(shares) | set(previous)
        if shares.get(nid, 0) != previous.get(nid, 0)
    }
    for nid in sorted(changed):
        revise_statement(state, dataset, nid, reason + "；共享搬运分摊调整")
    return changed


# ---------------------------------------------------------------- 接受方案


def accept_plan(state_path, plan_id: str) -> dict:
    """接受备选方案：核对当前可用条件，全部通过才形成预留并出具说明。"""
    state, dataset = _load_pair(state_path)
    plan = state["plans"].get(plan_id)
    if plan is None:
        raise ValueError(f"方案不存在：{plan_id}")
    if plan["status"] == "accepted":
        raise ValueError(f"方案 {plan_id} 已被接受，不能重复接受")

    flags = state["asset_flags"]
    reservations = state_mod.active_reservations(state)
    existing = _existing_by_need(state, dataset)
    as_of = dt.date.fromisoformat(state["business_day"])
    index = rules.build_compat_index(dataset.compats, as_of)
    conflicts: list = []

    seen: dict = {}
    for asg in plan["assignments"]:
        need = dataset.needs[asg["need"]]
        if state["need_overrides"].get(need.id) == "closed_new_purchase":
            conflicts.append(f"需求 {need.id} 已改用新购，不能再接受调剂预留")
            continue
        remaining = remaining_quantities(state, dataset, need)
        assignment_ok = True
        chosen: dict = {}
        for kind, ids in sorted(asg["items"].items()):
            if len(ids) > remaining.get(kind, 0):
                conflicts.append(
                    f"需求 {need.id} 组件 {kind} 剩余缺口 {remaining.get(kind, 0)} 件，"
                    f"本方案要求 {len(ids)} 件：需求已被其他方案覆盖"
                )
                assignment_ok = False
            chosen[kind] = []
            for aid in ids:
                if aid in seen:
                    conflicts.append(
                        f"资产 {aid} 在方案内被同时指派给需求 {seen[aid]} 与 {need.id}"
                    )
                    assignment_ok = False
                seen[aid] = need.id
                asset = dataset.assets[aid]
                ok, reason = rules.committable(dataset, asset, flags)
                if not ok:
                    conflicts.append(f"资产 {aid}：{reason}")
                    assignment_ok = False
                    continue
                ready = rules.effective_ready_day(dataset, asset, need, reservations)
                if ready > need.deadline:
                    conflicts.append(
                        f"资产 {aid} 当前最早可交付 {ready.isoformat()}，"
                        f"晚于需求 {need.id} 最迟期限 {need.deadline.isoformat()}"
                        "（在效预留或占用变化）"
                    )
                    assignment_ok = False
                chosen[kind].append(asset)
        if assignment_ok:
            # 套件兼容连同该需求已在用资产一并复核
            merged = {
                kind: list(existing.get(need.id, {}).get(kind, []))
                + list(chosen.get(kind, []))
                for kind in set(existing.get(need.id, {})) | set(chosen)
            }
            ok, problems, _ = rules.suite_check(index, need.usage, merged)
            conflicts.extend(f"需求 {need.id}：{p}" for p in problems)

    if conflicts:
        plan["last_conflicts"] = conflicts
        state_mod.save_state(state_path, state)
        return {"ok": False, "plan": plan_id, "conflicts": conflicts}

    day = state["business_day"]
    created_reservations = []
    for asg in plan["assignments"]:
        need = dataset.needs[asg["need"]]
        for kind, ids in sorted(asg["items"].items()):
            for aid in ids:
                buffer = rules.buffer_days(dataset, aid)
                res = {
                    "id": state_mod.next_id(state, "reservation", "RSV"),
                    "asset": aid,
                    "need": need.id,
                    "plan": plan_id,
                    "component": kind,
                    "from_day": asg["arrival"],
                    "until_release": (
                        need.end_day + dt.timedelta(days=buffer)
                    ).isoformat(),
                    "buffer_days": buffer,
                    "status": "active",
                    "released_by": None,
                }
                state["reservations"].append(res)
                created_reservations.append(res["id"])
    plan["status"] = "accepted"
    plan["accepted_day"] = day
    plan["last_conflicts"] = []

    covered = [asg["need"] for asg in plan["assignments"]]
    groups = set()
    for nid in covered:
        proc = dataset.procurement_of(nid)
        if proc and proc.approved and proc.shared_trip:
            groups.add(proc.shared_trip)
    revised = set()
    for group in sorted(groups):
        revised |= refresh_group(state, dataset, group, f"方案 {plan_id} 接受")
    for nid in sorted(covered):
        proc = dataset.procurement_of(nid)
        if proc and proc.approved and nid not in revised:
            stmt = revise_statement(state, dataset, nid, f"方案 {plan_id} 接受")
            if stmt:
                revised.add(nid)

    state_mod.save_state(state_path, state)
    return {
        "ok": True,
        "plan": plan_id,
        "reservations": created_reservations,
        "statements": [
            s["id"] for s in state["statements"] if s["need"] in set(covered) and s["status"] == "active"
        ],
        "revised_needs": sorted(revised),
    }


# ---------------------------------------------------------------- 交接登记


def register_handover(
    state_path,
    event_id: str | None = None,
    *,
    asset: str | None = None,
    kind: str | None = None,
    need: str | None = None,
    note: str = "",
    known_at: str | None = None,
) -> dict:
    """登记交接事实并结算影响：释放预留、重现缺口、修订说明。"""
    state, dataset = _load_pair(state_path)
    if event_id is not None:
        found = [h for h in dataset.handovers if h.id == event_id]
        if not found:
            raise ValueError(f"资料中无交接事件 {event_id}")
        event = found[0]
    else:
        if not kind:
            raise ValueError("手工登记必须给出 kind")
        if kind in ("inspection_failed", "return_requested") and not asset:
            raise ValueError(f"交接类型 {kind} 必须指明资产")
        event = domain.Handover(
            id=state_mod.next_id(state, "handover", "HX"),
            asset=asset,
            known_at=dt.datetime.fromisoformat(known_at)
            if known_at
            else dt.datetime.fromisoformat(state["business_day"] + "T09:00:00+08:00"),
            kind=kind,
            need=need,
            note=note,
        )
    if event.kind not in HANDOVER_KINDS:
        raise ValueError(f"未知交接类型 {event.kind}；可选：{sorted(HANDOVER_KINDS)}")
    if any(h["id"] == event.id for h in state["handovers"]):
        raise ValueError(f"交接事件 {event.id} 已登记，不能重复登记")
    if event.asset is not None and event.asset not in dataset.assets:
        raise ValueError(f"资产不存在：{event.asset}")
    if event.need is not None and event.need not in dataset.needs:
        raise ValueError(f"需求不存在：{event.need}")
    if event.kind == "switched_to_new_purchase" and event.need is None:
        raise ValueError("改用新购必须指明需求")

    released: list = []
    affected: set = set()
    label = HANDOVER_KINDS[event.kind]

    def release(res):
        res["status"] = "released"
        res["released_by"] = event.id
        released.append(res["id"])

    if event.kind in ("inspection_failed", "return_requested"):
        state["asset_flags"][event.asset] = (
            "inspection_failed"
            if event.kind == "inspection_failed"
            else "returned_pending_check"
        )
        hits = [
            r
            for r in state_mod.active_reservations(state)
            if r["asset"] == event.asset
        ]
        for hit in hits:
            target = dataset.needs[hit["need"]]
            if target.splittable:
                release(hit)
                affected.add(hit["need"])
            else:
                # 不拆分需求任一部件失效，整组预留解除，完整缺口重新出现
                for res in state_mod.active_reservations(state):
                    if res["need"] == hit["need"]:
                        release(res)
                affected.add(hit["need"])
    else:  # switched_to_new_purchase
        state["need_overrides"][event.need] = "closed_new_purchase"
        for res in state_mod.active_reservations(state):
            if res["need"] == event.need:
                release(res)
        affected.add(event.need)

    groups = set()
    for nid in affected:
        proc = dataset.procurement_of(nid)
        if proc and proc.approved and proc.shared_trip:
            groups.add(proc.shared_trip)
    revised: set = set()
    for group in sorted(groups):
        revised |= refresh_group(
            state, dataset, group, f"交接事件 {event.id}（{label}）"
        )
    for nid in sorted(affected):
        proc = dataset.procurement_of(nid)
        if proc and proc.approved and nid not in revised:
            stmt = revise_statement(
                state, dataset, nid, f"交接事件 {event.id}（{label}）"
            )
            if stmt:
                revised.add(nid)

    record = {
        "id": event.id,
        "asset": event.asset,
        "kind": event.kind,
        "kind_label": label,
        "need": event.need,
        "note": event.note,
        "known_at": event.known_at.isoformat(),
        "registered_day": state["business_day"],
        "effects": {
            "released_reservations": released,
            "affected_needs": sorted(affected),
            "revised_needs": sorted(revised),
            "asset_flag": state["asset_flags"].get(event.asset),
            "need_override": state["need_overrides"].get(event.need)
            if event.need
            else None,
        },
    }
    state["handovers"].append(record)
    state_mod.save_state(state_path, state)
    return record


def import_handovers(state_path) -> list:
    """按获知时间顺序登记资料中的全部交接事件。"""
    state, dataset = _load_pair(state_path)
    pending = sorted(
        (
            h
            for h in dataset.handovers
            if not any(r["id"] == h.id for r in state["handovers"])
        ),
        key=lambda h: (h.known_at, h.id),
    )
    records = []
    for event in pending:
        records.append(register_handover(state_path, event_id=event.id))
    return records


# ---------------------------------------------------------------- 状态与审计


def status_view(state_path) -> dict:
    state, dataset = _load_pair(state_path)
    needs_view = []
    for need in sorted(dataset.needs.values(), key=lambda n: n.id):
        scope, assets = current_scope(state, need.id)
        fraction, _ = savings.recognized_fraction(need, scope)
        override = state["need_overrides"].get(need.id)
        if override == "closed_new_purchase":
            status = "已改新购"
        elif fraction == 1:
            status = "已满足"
        elif sum(scope.values()) > 0:
            status = "部分到位" if need.splittable else "套件不完整（待重新方案）"
        else:
            status = "未解决"
        stmt = state_mod.active_statement(state, need.id)
        needs_view.append(
            {
                "need": need.id,
                "department": need.department,
                "usage": need.usage,
                "priority": need.priority,
                "deadline": need.deadline.isoformat(),
                "status": status,
                "scope": scope,
                "assets": sorted(assets),
                "statement_net_cents": stmt["net_cents"] if stmt else None,
                "statement_id": stmt["id"] if stmt else None,
            }
        )
    net_total = sum(
        s["net_cents"] for s in state["statements"] if s["status"] == "active"
    )
    groups_view = []
    groups = sorted(
        {p.shared_trip for p in dataset.procurement_by_need.values() if p.shared_trip}
    )
    for group in groups:
        alloc = state_mod.active_allocation(state, group)
        shares = alloc["shares"] if alloc else {}
        trip = planning._group_trip_cents(dataset, group) or 0
        groups_view.append(
            {
                "group": group,
                "allocation_id": alloc["id"] if alloc else None,
                "shares": shares,
                "trip_cents": trip,
                "consistent": (not shares) or sum(shares.values()) == trip,
            }
        )
    return {
        "business_day": state["business_day"],
        "data_version": state["data_version"],
        "needs": needs_view,
        "net_savings_cents": net_total,
        "groups": groups_view,
        "asset_flags": dict(state["asset_flags"]),
        "handovers_registered": len(state["handovers"]),
        "plans": {
            pid: p["status"] for pid, p in sorted(state["plans"].items())
        },
    }


def audit_need(state_path, need_id: str) -> dict:
    state, dataset = _load_pair(state_path)
    if need_id not in dataset.needs:
        raise ValueError(f"需求不存在：{need_id}")
    need = dataset.needs[need_id]
    proc = dataset.procurement_of(need_id)
    scope, assets = current_scope(state, need_id)
    fraction, basis = savings.recognized_fraction(need, scope)
    reservations = [r for r in state["reservations"] if r["need"] == need_id]
    statements = [s for s in state["statements"] if s["need"] == need_id]
    group = proc.shared_trip if proc else None
    allocations = [
        a for a in state["allocations"] if group and a["group"] == group
    ]
    plans = [
        {
            "id": p["id"],
            "status": p["status"],
            "strategy": p["strategy"],
            "assignments": [a for a in p["assignments"] if a["need"] == need_id],
        }
        for p in state["plans"].values()
        if any(a["need"] == need_id for a in p["assignments"])
    ]
    asset_set = {r["asset"] for r in reservations}
    handovers = [
        h
        for h in state["handovers"]
        if h["need"] == need_id or h["asset"] in asset_set
    ]
    return {
        "need": need,
        "procurement": proc,
        "status_fraction": (fraction.numerator, fraction.denominator),
        "fraction_basis": basis,
        "scope": scope,
        "assets": sorted(assets),
        "reservations": reservations,
        "statements": statements,
        "allocations": allocations,
        "plans": plans,
        "handovers": handovers,
        "override": state["need_overrides"].get(need_id),
    }


def audit_asset(state_path, asset_id: str) -> dict:
    state, dataset = _load_pair(state_path)
    if asset_id not in dataset.assets:
        raise ValueError(f"资产不存在：{asset_id}")
    asset = dataset.assets[asset_id]
    ok, reason = rules.committable(dataset, asset, state["asset_flags"])
    return {
        "asset": asset,
        "occupancies": dataset.occupancy_of(asset_id),
        "committable": ok,
        "reason": reason,
        "ready_day": rules.ready_day(dataset, asset).isoformat(),
        "flag": state["asset_flags"].get(asset_id),
        "reservations": [
            r for r in state["reservations"] if r["asset"] == asset_id
        ],
        "handovers": [h for h in state["handovers"] if h["asset"] == asset_id],
    }


# ---------------------------------------------------------------- 导出


def export_outputs(state_path, out_dir="outputs") -> list:
    """把当前说明（含历史版本）与汇总写成 outputs/ 下的 Markdown 文件。"""
    from . import report

    state, dataset = _load_pair(state_path)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    view = status_view(state_path)
    summary = out / "summary.md"
    summary.write_text(report.summary_markdown(view), encoding="utf-8")
    written.append(str(summary))

    stmt_dir = out / "statements"
    stmt_dir.mkdir(exist_ok=True)
    need_ids = sorted({s["need"] for s in state["statements"]})
    for nid in need_ids:
        statements = [s for s in state["statements"] if s["need"] == nid]
        path = stmt_dir / f"{nid}.md"
        path.write_text(
            report.statement_markdown(nid, statements, dataset), encoding="utf-8"
        )
        written.append(str(path))

    alloc_path = out / "allocations.md"
    lines = ["# 共享搬运分摊记录", ""]
    for alloc in state["allocations"]:
        lines.append(
            f"## {alloc['id']}　组 {alloc['group']} 第 {alloc['version']} 版（{alloc['status']}）"
        )
        lines.append("")
        lines.append(f"- 出具日：{alloc['issued_day']}　事由：{alloc['reason']}")
        lines.append(f"- 整组费用：{report.yuan(alloc['trip_cents'])} 元")
        lines.append(f"- 分摊：{alloc['shares'] or '（无认定需求）'}")
        lines.append(f"- 依据：{alloc['basis']}")
        lines.append("")
    alloc_path.write_text("\n".join(lines), encoding="utf-8")
    written.append(str(alloc_path))
    return written
