"""文字与 Markdown 呈现：方案比较、状态、说明与审计视图。"""
from __future__ import annotations

from . import domain


def yuan(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"{cents / 100:,.2f}"


def _row(cells, widths) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()


def render_plan_comparison(plans: list, dataset) -> str:
    lines = ["备选方案比较（备选之间可共享设备，仅被接受的方案形成预留）", ""]
    if not plans:
        lines.append("当前没有可生成的新方案。")
        return "\n".join(lines)
    widths = [10, 15, 9, 9, 14, 12]
    lines.append(
        _row(["方案", "策略", "整组满足", "部分到位", "估算净节支(元)", "状态"], widths)
    )
    for plan in plans:
        est = plan["estimate"]
        lines.append(
            _row(
                [
                    plan["id"],
                    plan["strategy"],
                    est["covered_full"],
                    est["covered_partial"],
                    yuan(est["recognized_net_cents"]),
                    plan["status"],
                ],
                widths,
            )
        )
    lines.append("")
    lines.append("策略说明：")
    for name, note in sorted({p["strategy"]: p["strategy_note"] for p in plans}.items()):
        lines.append(f"  {name}：{note}")
    return "\n".join(lines)


def render_plan_detail(plan: dict, dataset) -> str:
    lines = [
        f"方案 {plan['id']}（策略 {plan['strategy']}：{plan['strategy_note']}）",
        f"状态：{plan['status']}　生成日：{plan['created_day']}",
        "",
        "指派明细：",
    ]
    for asg in plan["assignments"]:
        need = dataset.needs[asg["need"]]
        est = plan["estimate"]["per_need"].get(asg["need"], {})
        items = "、".join(
            f"{kind}×{len(ids)}[{','.join(ids)}]"
            for kind, ids in sorted(asg["items"].items())
        )
        lines.append(
            f"  需求 {asg['need']}（{need.department}，优先级 {need.priority}，"
            f"期限 {need.deadline.isoformat()}）"
        )
        lines.append(f"    {items}")
        lines.append(
            f"    到位 {asg['arrival']}　必要维修 {yuan(asg['repair_cents'])} 元　"
            f"兼容依据 {','.join(asg['evidence']) if asg['evidence'] else '无需配对'}"
        )
        if est:
            approved = "已获准" if est.get("approved") else "未获准新购（不认定节支）"
            lines.append(
                f"    认定 {est.get('fraction_num')}/{est.get('fraction_den')}（{approved}）"
                f"　毛节支 {yuan(est.get('gross_cents'))}　交付安装 {yuan(est.get('delivery_cents'))}"
                f"　搬运分摊 {yuan(est.get('trip_share_cents'))}　净节支 {yuan(est.get('net_cents'))} 元"
            )
    if plan["uncovered"]:
        lines.append("")
        lines.append("未覆盖需求：")
        for item in plan["uncovered"]:
            lines.append(f"  {item['need']}：{item['reason']}")
    if plan.get("last_conflicts"):
        lines.append("")
        lines.append("上次接受核对发现的冲突：")
        for conflict in plan["last_conflicts"]:
            lines.append(f"  - {conflict}")
    return "\n".join(lines)


def render_status(view: dict) -> str:
    lines = [
        f"当前状态（业务日 {view['business_day']}，资料版本 {view['data_version']}）",
        "",
        _row(
            ["需求", "部门", "用途", "优先级", "期限", "状态", "在效资产", "认定净节支(元)"],
            [6, 6, 8, 6, 12, 18, 30, 14],
        ),
    ]
    for item in view["needs"]:
        lines.append(
            _row(
                [
                    item["need"],
                    item["department"],
                    item["usage"],
                    item["priority"],
                    item["deadline"],
                    item["status"],
                    ",".join(item["assets"]) or "—",
                    yuan(item["statement_net_cents"]),
                ],
                [6, 6, 8, 6, 12, 18, 30, 14],
            )
        )
    lines.append("")
    lines.append(f"净节支合计：{yuan(view['net_savings_cents'])} 元")
    lines.append("")
    lines.append("共享搬运组：")
    for group in view["groups"]:
        shares = "、".join(
            f"{nid}:{yuan(amt)}" for nid, amt in sorted(group["shares"].items())
        ) or "（当前无认定需求）"
        check = "一致" if group["consistent"] else "不一致！"
        lines.append(
            f"  {group['group']}：整组 {yuan(group['trip_cents'])} 元　分摊 {shares}　合计校验{check}"
        )
    if view["asset_flags"]:
        lines.append("")
        lines.append("交接后资产结论：")
        for aid, flag in sorted(view["asset_flags"].items()):
            lines.append(f"  {aid}：{flag}")
    return "\n".join(lines)


def render_statement(stmt: dict, dataset) -> str:
    need = dataset.needs[stmt["need"]]
    proc = dataset.procurement_of(stmt["need"])
    lines = [
        f"节支说明 {stmt['id']}（需求 {stmt['need']} 第 {stmt['version']} 版，{stmt['status']}）",
        f"  出具日 {stmt['issued_day']}　事由：{stmt['reason']}",
        f"  部门 {need.department}　用途 {need.usage}　采购批准 {proc.id if proc else '无'}",
        f"  认定范围 {stmt['scope']}　比例 {stmt['fraction_num']}/{stmt['fraction_den']}",
        f"  资产 {','.join(stmt['assets']) or '无'}",
        f"  毛节支 {yuan(stmt['gross_cents'])} − 维修 {yuan(stmt['repair_cents'])}"
        f" − 交付安装 {yuan(stmt['delivery_cents'])} − 搬运分摊 {yuan(stmt['trip_share_cents'])}"
        f" = 净节支 {yuan(stmt['net_cents'])} 元",
        f"  口径：{stmt['basis']['fraction']}",
        f"　　　{stmt['basis']['repair']}",
        f"　　　{stmt['basis']['delivery']}",
        f"　　　{stmt['basis']['trip']}",
    ]
    if stmt["supersedes"]:
        lines.append(f"  替代上一版 {stmt['supersedes']}（旧版仍可复核）")
    return "\n".join(lines)


def render_statements(statements: list, dataset) -> str:
    if not statements:
        return "尚无节支说明。"
    return "\n\n".join(render_statement(s, dataset) for s in statements)


def render_audit_need(audit: dict) -> str:
    need: domain.Need = audit["need"]
    proc = audit["procurement"]
    num, den = audit["status_fraction"]
    lines = [
        f"需求 {need.id} 审计视图",
        f"  部门 {need.department}　用途 {need.usage}　数量 {need.quantities}"
        f"　{'可拆分' if need.splittable else '不拆分'}",
        f"  使用区间 {need.start_day.isoformat()}~{need.end_day.isoformat()}"
        f"　最迟期限 {need.deadline.isoformat()}　优先级 {need.priority}",
        f"  采购批准 {proc.id if proc else '无'}："
        + (
            f"{'已获准' if proc.approved else '未获准'}，可比新购价 {yuan(proc.comparable_cents)} 元"
            if proc
            else ""
        ),
        f"  当前认定比例 {num}/{den}（{audit['fraction_basis']}）"
        f"　在效资产 {','.join(audit['assets']) or '无'}",
    ]
    if audit["override"]:
        lines.append(f"  需求结论：{audit['override']}")
    lines.append("")
    lines.append("  预留记录：")
    for res in audit["reservations"]:
        lines.append(
            f"    {res['id']} 资产 {res['asset']}（{res['component']}）"
            f" 方案 {res['plan']} 窗口 {res['from_day']}~{res['until_release']}"
            f" {res['status']}{'　解除于 ' + res['released_by'] if res['released_by'] else ''}"
        )
    if not audit["reservations"]:
        lines.append("    （无）")
    lines.append("  方案记录：")
    for plan in audit["plans"]:
        lines.append(f"    {plan['id']}（{plan['strategy']}，{plan['status']}）")
    if not audit["plans"]:
        lines.append("    （无）")
    lines.append("  交接事件：")
    for hand in audit["handovers"]:
        lines.append(
            f"    {hand['id']} {hand['kind_label']} 资产 {hand['asset']}"
            f"　登记日 {hand['registered_day']}　{hand['note']}"
        )
    if not audit["handovers"]:
        lines.append("    （无）")
    lines.append("")
    lines.append("  节支说明版本：")
    for stmt in audit["statements"]:
        lines.append(
            f"    {stmt['id']} 第{stmt['version']}版 {stmt['status']}"
            f"　净节支 {yuan(stmt['net_cents'])} 元　{stmt['reason']}"
        )
    if not audit["statements"]:
        lines.append("    （无）")
    lines.append("  共享搬运分摊版本：")
    for alloc in audit["allocations"]:
        lines.append(
            f"    {alloc['id']} 第{alloc['version']}版 {alloc['status']}"
            f"　分摊 {alloc['shares']}　{alloc['basis']}"
        )
    if not audit["allocations"]:
        lines.append("    （无）")
    return "\n".join(lines)


def render_audit_asset(audit: dict) -> str:
    asset: domain.Asset = audit["asset"]
    lines = [
        f"资产 {asset.id} 审计视图",
        f"  类别 {asset.kind}　型号 {asset.model}　状态 {asset.state}"
        f"　检测得分 {asset.condition_score if asset.condition_score is not None else '未知'}",
        f"  存放地 {asset.location}　最早释放 {asset.available_day.isoformat()}"
        f"　可交付 {audit['ready_day']}",
        f"  维修报价 {yuan(asset.repair_cents)} 元 / {asset.repair_days} 天",
        f"  当前可否承诺：{'是' if audit['committable'] else '否'}（{audit['reason']}）",
    ]
    if audit["flag"]:
        lines.append(f"  交接结论：{audit['flag']}")
    for occ in audit["occupancies"]:
        lines.append(
            f"  占用 {occ.id}：{occ.department} 用至 {occ.until_day.isoformat()}"
            f"，返还检测 {occ.return_check_days} 天，运输 {occ.transport_days} 天"
        )
    lines.append("  预留记录：")
    for res in audit["reservations"]:
        lines.append(
            f"    {res['id']} 需求 {res['need']} 方案 {res['plan']}"
            f" 窗口 {res['from_day']}~{res['until_release']} {res['status']}"
        )
    if not audit["reservations"]:
        lines.append("    （无）")
    lines.append("  交接事件：")
    for hand in audit["handovers"]:
        lines.append(
            f"    {hand['id']} {hand['kind_label']}　登记日 {hand['registered_day']}　{hand['note']}"
        )
    if not audit["handovers"]:
        lines.append("    （无）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 导出


def statement_markdown(need_id: str, statements: list, dataset) -> str:
    need = dataset.needs[need_id]
    proc = dataset.procurement_of(need_id)
    lines = [
        f"# 需求 {need_id} 节支说明（含历史版本）",
        "",
        f"- 部门：{need.department}　用途：{need.usage}　数量：{need.quantities}",
        f"- 采购批准：{proc.id if proc else '无'}"
        + (f"（{'已获准' if proc.approved else '未获准'}，可比新购价 {yuan(proc.comparable_cents)} 元）" if proc else ""),
        "",
    ]
    for stmt in statements:
        lines.append(f"## 第 {stmt['version']} 版（{stmt['status']}）　{stmt['id']}")
        lines.append("")
        lines.append(f"- 出具日：{stmt['issued_day']}　事由：{stmt['reason']}")
        if stmt["supersedes"]:
            lines.append(f"- 替代：{stmt['supersedes']}（旧版仍可复核）")
        lines.append(
            f"- 认定范围：{stmt['scope']}　比例 {stmt['fraction_num']}/{stmt['fraction_den']}"
        )
        lines.append(f"- 资产：{', '.join(stmt['assets']) or '无'}")
        lines.append(
            f"- 毛节支 {yuan(stmt['gross_cents'])} − 维修 {yuan(stmt['repair_cents'])}"
            f" − 交付安装 {yuan(stmt['delivery_cents'])} − 搬运分摊 {yuan(stmt['trip_share_cents'])}"
            f" = **净节支 {yuan(stmt['net_cents'])} 元**"
        )
        lines.append("- 口径：")
        lines.append(f"  - {stmt['basis']['fraction']}")
        lines.append(f"  - {stmt['basis']['repair']}")
        lines.append(f"  - {stmt['basis']['delivery']}")
        lines.append(f"  - {stmt['basis']['trip']}")
        lines.append("")
    return "\n".join(lines)


def summary_markdown(view: dict) -> str:
    lines = [
        "# 调剂认定汇总",
        "",
        f"- 业务日：{view['business_day']}　资料版本：{view['data_version']}",
        f"- 净节支合计：**{yuan(view['net_savings_cents'])} 元**",
        "",
        "| 需求 | 部门 | 状态 | 在效资产 | 认定净节支(元) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in view["needs"]:
        lines.append(
            f"| {item['need']} | {item['department']} | {item['status']} "
            f"| {','.join(item['assets']) or '—'} | {yuan(item['statement_net_cents'])} |"
        )
    lines.append("")
    lines.append("## 共享搬运分摊")
    lines.append("")
    for group in view["groups"]:
        shares = "、".join(
            f"{nid}:{yuan(amt)}元" for nid, amt in sorted(group["shares"].items())
        ) or "（当前无认定需求）"
        lines.append(
            f"- {group['group']}：整组 {yuan(group['trip_cents'])} 元，分摊 {shares}"
            f"（合计校验{'一致' if group['consistent'] else '不一致'}）"
        )
    return "\n".join(lines)
