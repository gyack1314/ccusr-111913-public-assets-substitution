"""命令行入口：python3 -m app.cli <子命令>。

办理顺序：import → plan → accept → handover-import / handover → status / audit。
"""
from __future__ import annotations

import argparse
import sys

from . import operations, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-coord",
        description="公物仓成套调剂的需求替代与节支认定",
    )
    parser.add_argument(
        "--state",
        default=".local/state.json",
        help="运行时状态文件（默认 .local/state.json）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="校验并导入本地资料，建立初始状态")
    p.add_argument("--business-day", default=None, help="业务日 YYYY-MM-DD，缺省取资料最晚日期")

    p = sub.add_parser("plan", help="生成备选方案（备选之间可共享设备）")
    p.add_argument(
        "--strategy",
        action="append",
        default=None,
        help="只生成指定策略，可重复；缺省生成全部策略",
    )

    sub.add_parser("plans", help="列出备选方案比较")

    p = sub.add_parser("show-plan", help="查看方案明细")
    p.add_argument("plan_id")

    p = sub.add_parser("accept", help="核对当前条件并接受方案，形成预留")
    p.add_argument("plan_id")

    sub.add_parser("handover-import", help="按获知时间登记资料中的全部交接事件")

    p = sub.add_parser("handover", help="登记单项交接事实")
    p.add_argument("--event", default=None, help="资料中的交接事件id（如 H1）")
    p.add_argument("--asset", default=None)
    p.add_argument(
        "--kind",
        default=None,
        choices=sorted(operations.HANDOVER_KINDS),
        help="交接类型",
    )
    p.add_argument("--need", default=None)
    p.add_argument("--note", default="")
    p.add_argument("--known-at", default=None, help="获知时间 ISO 时间戳")

    sub.add_parser("status", help="当前已满足需求与净节支")

    p = sub.add_parser("statements", help="查看节支说明")
    p.add_argument("--need", default=None, help="只看指定需求")
    p.add_argument("--all", action="store_true", help="含历史版本")

    p = sub.add_parser("audit", help="审计视图：需求或资产")
    p.add_argument("target", help="需求id（如 N1）或资产id（如 A001）")

    p = sub.add_parser("export", help="导出 Markdown 说明到 outputs/")
    p.add_argument("--out", default="outputs", help="输出目录（默认 outputs/）")

    sub.add_parser("reset", help="清除运行时状态（不改写 data/ 原始资料）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    state_path = args.state
    try:
        if args.cmd == "import":
            result = operations.import_data(state_path, args.business_day)
            print(
                f"已导入资料版本 {result['data_version']}，业务日 {result['business_day']}，"
                f"需求 {result['needs']} 项，资产 {result['assets']} 台"
            )
        elif args.cmd == "plan":
            plans = operations.generate_plans(state_path, args.strategy)
            dataset = operations.load_dataset()
            print(report.render_plan_comparison(plans, dataset))
        elif args.cmd == "plans":
            state = operations.state_mod.load_state(state_path)
            dataset = operations.load_dataset()
            proposed = [
                p for p in sorted(state["plans"].values(), key=lambda p: p["id"])
            ]
            print(report.render_plan_comparison(proposed, dataset))
        elif args.cmd == "show-plan":
            state = operations.state_mod.load_state(state_path)
            plan = state["plans"].get(args.plan_id)
            if plan is None:
                raise ValueError(f"方案不存在：{args.plan_id}")
            dataset = operations.load_dataset()
            print(report.render_plan_detail(plan, dataset))
        elif args.cmd == "accept":
            result = operations.accept_plan(state_path, args.plan_id)
            if result["ok"]:
                print(
                    f"方案 {result['plan']} 已接受：形成预留 {len(result['reservations'])} 项，"
                    f"涉及需求说明 {len(result['statements'])} 份"
                )
                print("在效说明：" + ("、".join(result["statements"]) or "无"))
            else:
                print(f"方案 {result['plan']} 未通过当前条件核对，未形成任何预留：")
                for conflict in result["conflicts"]:
                    print(f"  - {conflict}")
                return 1
        elif args.cmd == "handover-import":
            records = operations.import_handovers(state_path)
            if not records:
                print("资料中的交接事件均已登记。")
            for record in records:
                _print_handover(record)
        elif args.cmd == "handover":
            record = operations.register_handover(
                state_path,
                event_id=args.event,
                asset=args.asset,
                kind=args.kind,
                need=args.need,
                note=args.note,
                known_at=args.known_at,
            )
            _print_handover(record)
        elif args.cmd == "status":
            print(report.render_status(operations.status_view(state_path)))
        elif args.cmd == "statements":
            state = operations.state_mod.load_state(state_path)
            dataset = operations.load_dataset()
            statements = state["statements"]
            if args.need:
                statements = [s for s in statements if s["need"] == args.need]
            if not args.all:
                statements = [s for s in statements if s["status"] == "active"]
            print(report.render_statements(statements, dataset))
        elif args.cmd == "audit":
            dataset = operations.load_dataset()
            if args.target in dataset.assets:
                print(report.render_audit_asset(operations.audit_asset(state_path, args.target)))
            elif args.target in dataset.needs:
                print(report.render_audit_need(operations.audit_need(state_path, args.target)))
            else:
                raise ValueError(f"审计对象不存在：{args.target}")
        elif args.cmd == "export":
            written = operations.export_outputs(state_path, args.out)
            print("已写出：")
            for path in written:
                print(f"  {path}")
        elif args.cmd == "reset":
            result = operations.reset(state_path)
            print("已清除运行时状态。" if result["removed"] else "状态本就不存在。")
    except (ValueError, FileNotFoundError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


def _print_handover(record: dict) -> None:
    effects = record["effects"]
    asset = record["asset"] or "（整项需求）"
    print(f"已登记交接 {record['id']}（{record['kind_label']}，资产 {asset}）")
    print(f"  解除预留：{('、'.join(effects['released_reservations'])) or '无'}")
    print(f"  受影响需求：{('、'.join(effects['affected_needs'])) or '无'}")
    print(f"  修订说明需求：{('、'.join(effects['revised_needs'])) or '无'}")


if __name__ == "__main__":
    sys.exit(main())
