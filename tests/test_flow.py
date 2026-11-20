"""端到端业务流程测试：导入 → 方案 → 接受 → 交接 → 状态与审计。

使用仓库内真实合成资料，状态写入临时目录，用后自动清理。
"""
import pathlib
import tempfile
import unittest

from app import operations
from app import state as state_mod


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = pathlib.Path(self.tmp.name) / "state.json"
        operations.import_data(self.state_path)

    def state(self):
        return state_mod.load_state(self.state_path)

    def plan_assets(self, plan):
        return [
            aid
            for asg in plan["assignments"]
            for ids in asg["items"].values()
            for aid in ids
        ]


class ImportTests(unittest.TestCase):
    def test_import_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "state.json"
            result = operations.import_data(path)
            self.assertEqual(result["data_version"], "2026-09-synthetic-v1")
            # 业务日缺省取资料中最晚日期（H2 获知日 2026-09-22）
            self.assertEqual(result["business_day"], "2026-09-22")
            with self.assertRaises(ValueError):
                operations.import_data(path)  # 重复导入被拒绝


class PlanTests(FlowBase):
    def setUp(self):
        super().setUp()
        self.plans = operations.generate_plans(self.state_path)

    def test_plans_generated_deterministic(self):
        self.assertEqual(len(self.plans), 3)
        self.assertEqual(
            [p["strategy"] for p in self.plans],
            ["priority-first", "savings-first", "approved-first"],
        )

    def test_assignments_are_feasible(self):
        dataset = operations.load_dataset()
        for plan in self.plans:
            seen = set()
            for asg in plan["assignments"]:
                need = dataset.needs[asg["need"]]
                ids = [aid for ids in asg["items"].values() for aid in ids]
                # 方案内部同一资产只出现一次
                self.assertFalse(set(ids) & seen, f"{plan['id']} 内资产重复")
                seen |= set(ids)
                # 不拆分需求必须整组指派
                if not need.splittable:
                    for kind, qty in need.quantities.items():
                        self.assertEqual(len(asg["items"][kind]), qty)
                # 到位不晚于最迟期限
                self.assertLessEqual(asg["arrival"], need.deadline.isoformat())
                for aid in ids:
                    asset = dataset.assets[aid]
                    self.assertIn(asset.state, {"idle", "tested", "repairable"})
                    self.assertIsNotNone(asset.condition_score)

    def test_alternative_plans_may_share_assets(self):
        owners = {}
        shared = set()
        for plan in self.plans:
            for aid in self.plan_assets(plan):
                if aid in owners:
                    shared.add(aid)
                owners[aid] = plan["id"]
        self.assertTrue(shared, "备选方案之间应允许共享设备")

    def test_not_approved_needs_have_zero_estimate(self):
        for plan in self.plans:
            for nid, entry in plan["estimate"]["per_need"].items():
                proc = operations.load_dataset().procurement_of(nid)
                if not proc.approved:
                    self.assertEqual(entry["net_cents"], 0)
                    self.assertEqual(entry["gross_cents"], 0)


class AcceptTests(FlowBase):
    def test_accept_creates_reservations_and_statements(self):
        plans = operations.generate_plans(self.state_path)
        plan = plans[0]
        result = operations.accept_plan(self.state_path, plan["id"])
        self.assertTrue(result["ok"])
        state = self.state()
        active = [r for r in state["reservations"] if r["status"] == "active"]
        self.assertEqual(len(active), len(result["reservations"]))
        # 每个被覆盖且已获准的需求都有在效说明；未获准需求没有说明
        covered = {a["need"] for a in plan["assignments"]}
        dataset = operations.load_dataset()
        for nid in covered:
            stmt = state_mod.active_statement(state, nid)
            if dataset.procurement_of(nid).approved:
                self.assertIsNotNone(stmt, f"{nid} 应有节支说明")
            else:
                self.assertIsNone(stmt, f"{nid} 未获准新购，不应有节支说明")

    def test_estimate_matches_statement(self):
        plans = operations.generate_plans(self.state_path)
        plan = plans[0]
        operations.accept_plan(self.state_path, plan["id"])
        state = self.state()
        for nid, entry in plan["estimate"]["per_need"].items():
            stmt = state_mod.active_statement(state, nid)
            if stmt is None:
                continue
            self.assertEqual(
                stmt["net_cents"], entry["net_cents"], f"{nid} 估算与认定不一致"
            )

    def test_accept_twice_rejected(self):
        plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, plans[0]["id"])
        with self.assertRaises(ValueError):
            operations.accept_plan(self.state_path, plans[0]["id"])

    def test_conflicting_plan_fails_without_side_effects(self):
        plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, plans[0]["id"])
        before = len(self.state()["reservations"])
        result = operations.accept_plan(self.state_path, plans[1]["id"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflicts"])
        # 失败不产生任何预留变更
        self.assertEqual(len(self.state()["reservations"]), before)
        # 冲突说明指名具体资产或需求
        text = "\n".join(result["conflicts"])
        self.assertIn("A0", text)

    def test_group_trip_counted_once(self):
        plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, plans[0]["id"])
        state = self.state()
        dataset = operations.load_dataset()
        for group in {"G0", "G1", "G2"}:
            alloc = state_mod.active_allocation(state, group)
            trip = next(
                p.trip_cents
                for p in dataset.procurement_by_need.values()
                if p.shared_trip == group
            )
            if alloc is None:
                continue
            # 分摊合计严格等于整组费用，只计一次
            self.assertEqual(sum(alloc["shares"].values()), trip)


class HandoverTests(FlowBase):
    def setUp(self):
        super().setUp()
        self.plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, self.plans[0]["id"])

    def test_handover_import_reopens_gap_and_reallocates(self):
        # H2：A003（N12 在用）退回待检 → N12 缺口重现、说明归零、G0 重新分摊
        records = operations.import_handovers(self.state_path)
        self.assertEqual([r["id"] for r in records], ["H1", "H2"])
        state = self.state()
        self.assertEqual(state["asset_flags"].get("A001"), "inspection_failed")
        self.assertEqual(state["asset_flags"].get("A003"), "returned_pending_check")
        # N12 预留解除，说明归零
        active_n12 = [
            r
            for r in state["reservations"]
            if r["need"] == "N12" and r["status"] == "active"
        ]
        self.assertEqual(active_n12, [])
        stmt = state_mod.active_statement(state, "N12")
        self.assertEqual(stmt["net_cents"], 0)
        # G0 组内剩余认定需求均摊，合计仍等于整组费用
        alloc = state_mod.active_allocation(state, "G0")
        self.assertNotIn("N12", alloc["shares"])
        self.assertEqual(sum(alloc["shares"].values()), 15000)
        # 状态视图：N12 缺口重现
        view = operations.status_view(self.state_path)
        n12 = next(n for n in view["needs"] if n["need"] == "N12")
        self.assertEqual(n12["status"], "未解决")

    def test_double_registration_rejected(self):
        operations.import_handovers(self.state_path)
        with self.assertRaises(ValueError):
            operations.register_handover(self.state_path, event_id="H1")

    def test_stale_plan_accept_conflicts_after_handover(self):
        operations.import_handovers(self.state_path)
        # 含有已退回资产 A003 且尚未接受的备选方案，接受时必须报冲突
        state = self.state()
        stale = next(
            p
            for p in state["plans"].values()
            if p["status"] == "proposed"
            and "A003" in self.plan_assets(p)
        )
        result = operations.accept_plan(self.state_path, stale["id"])
        self.assertFalse(result["ok"])
        self.assertTrue(any("A003" in c for c in result["conflicts"]))

    def test_flagged_assets_excluded_from_new_plans(self):
        operations.import_handovers(self.state_path)
        new_plans = operations.generate_plans(self.state_path)
        for plan in new_plans:
            self.assertNotIn("A001", self.plan_assets(plan))
            self.assertNotIn("A003", self.plan_assets(plan))

    def test_statement_history_preserved(self):
        operations.import_handovers(self.state_path)
        state = self.state()
        versions = [s for s in state["statements"] if s["need"] == "N12"]
        self.assertGreaterEqual(len(versions), 2)
        old = versions[0]
        self.assertEqual(old["status"], "superseded")
        # 旧版按当时交接与价格仍可复核
        self.assertGreater(old["net_cents"], 0)
        self.assertEqual(old["assets"], ["A003"])


class PartialReleaseTests(FlowBase):
    def _accept_plan_covering(self, need_id):
        plans = operations.generate_plans(self.state_path)
        for plan in plans:
            if any(a["need"] == need_id for a in plan["assignments"]):
                operations.accept_plan(self.state_path, plan["id"])
                return plan
        self.fail(f"没有覆盖 {need_id} 的方案")

    def test_splittable_partial_release(self):
        plan = self._accept_plan_covering("N4")
        asg = next(a for a in plan["assignments"] if a["need"] == "N4")
        n4_assets = [aid for ids in asg["items"].values() for aid in ids]
        asset = n4_assets[0]
        before = self.state()
        stmt_before = state_mod.active_statement(before, "N4")
        operations.register_handover(
            self.state_path,
            asset=asset,
            kind="return_requested",
            need="N4",
            note="测试：部分退回",
        )
        state = self.state()
        active_n4 = [
            r
            for r in state["reservations"]
            if r["need"] == "N4" and r["status"] == "active"
        ]
        # 可拆分需求只解除受影响资产，其余预留保留
        self.assertEqual(len(active_n4), len(n4_assets) - 1)
        stmt = state_mod.active_statement(state, "N4")
        # 认定范围随之缩小，被退回资产不再计入
        self.assertLess(
            stmt["fraction_num"] / stmt["fraction_den"],
            stmt_before["fraction_num"] / stmt_before["fraction_den"],
        )
        self.assertEqual(len(stmt["assets"]), len(stmt_before["assets"]) - 1)
        self.assertNotIn(asset, stmt["assets"])

    def test_non_splittable_full_release(self):
        plan = self._accept_plan_covering("N1")
        asg = next(a for a in plan["assignments"] if a["need"] == "N1")
        asset = next(iter(asg["items"].values()))[0]
        operations.register_handover(
            self.state_path,
            asset=asset,
            kind="inspection_failed",
            need="N1",
            note="测试：关键设备不适用",
        )
        state = self.state()
        active_n1 = [
            r
            for r in state["reservations"]
            if r["need"] == "N1" and r["status"] == "active"
        ]
        # 不拆分需求任一部件失效，整组预留解除
        self.assertEqual(active_n1, [])
        stmt = state_mod.active_statement(state, "N1")
        self.assertEqual(stmt["net_cents"], 0)
        view = operations.status_view(self.state_path)
        n1 = next(n for n in view["needs"] if n["need"] == "N1")
        self.assertEqual(n1["status"], "未解决")

    def test_switched_to_new_purchase(self):
        plan = self._accept_plan_covering("N3")
        operations.register_handover(
            self.state_path,
            kind="switched_to_new_purchase",
            need="N3",
            note="测试：改用新购",
        )
        state = self.state()
        self.assertEqual(state["need_overrides"].get("N3"), "closed_new_purchase")
        active_n3 = [
            r
            for r in state["reservations"]
            if r["need"] == "N3" and r["status"] == "active"
        ]
        self.assertEqual(active_n3, [])
        stmt = state_mod.active_statement(state, "N3")
        self.assertEqual(stmt["net_cents"], 0)
        # 已改新购的需求不再进入新方案
        new_plans = operations.generate_plans(self.state_path)
        for p in new_plans:
            self.assertNotIn("N3", {a["need"] for a in p["assignments"]})
        view = operations.status_view(self.state_path)
        n3 = next(n for n in view["needs"] if n["need"] == "N3")
        self.assertEqual(n3["status"], "已改新购")


class TopUpTests(FlowBase):
    def test_topup_plan_compatible_with_in_use_assets(self):
        # 接受 approved-first 方案后，N8 部分到位（显示器+转接头已在用）
        plans = operations.generate_plans(self.state_path)
        plan = next(p for p in plans if p["strategy"] == "approved-first")
        operations.accept_plan(self.state_path, plan["id"])
        dataset = operations.load_dataset()
        import datetime as dt

        from app import rules

        index = rules.build_compat_index(dataset.compats, dt.date(2026, 9, 22))
        topups = operations.generate_plans(self.state_path)
        for p in topups:
            for asg in p["assignments"]:
                existing = {}
                for res in state_mod.active_reservations(self.state()):
                    if res["need"] == asg["need"]:
                        existing.setdefault(res["component"], []).append(
                            dataset.assets[res["asset"]]
                        )
                chosen = {
                    k: [dataset.assets[a] for a in ids]
                    for k, ids in asg["items"].items()
                }
                merged = {
                    k: existing.get(k, []) + chosen.get(k, [])
                    for k in set(existing) | set(chosen)
                }
                ok, problems, _ = rules.suite_check(
                    index, dataset.needs[asg["need"]].usage, merged
                )
                self.assertTrue(ok, f"{p['id']} 补足组合与在用资产不兼容：{problems}")

    def test_topup_accept_revises_statement_and_matches_estimate(self):
        plans = operations.generate_plans(self.state_path)
        plan = next(p for p in plans if p["strategy"] == "approved-first")
        operations.accept_plan(self.state_path, plan["id"])
        operations.import_handovers(self.state_path)  # H2 退回 N12 的空调
        topups = operations.generate_plans(self.state_path)
        top = next(
            (
                p
                for p in topups
                if any(a["need"] == "N12" for a in p["assignments"])
            ),
            None,
        )
        self.assertIsNotNone(top, "退回后应能生成补足方案")
        result = operations.accept_plan(self.state_path, top["id"])
        self.assertTrue(result["ok"])
        stmt = state_mod.active_statement(self.state(), "N12")
        self.assertEqual((stmt["fraction_num"], stmt["fraction_den"]), (1, 1))
        # 方案估算与接受后的认定一致
        est = top["estimate"]["per_need"]["N12"]
        self.assertEqual(est["net_cents"], stmt["net_cents"])
        # 退回的 A003 不得再被使用
        self.assertNotIn("A003", stmt["assets"])


class StatusTests(FlowBase):
    def test_net_savings_equals_active_statements(self):
        plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, plans[0]["id"])
        operations.import_handovers(self.state_path)
        view = operations.status_view(self.state_path)
        state = self.state()
        total = sum(
            s["net_cents"] for s in state["statements"] if s["status"] == "active"
        )
        self.assertEqual(view["net_savings_cents"], total)
        for group in view["groups"]:
            self.assertTrue(group["consistent"])

    def test_audit_traces_need(self):
        plans = operations.generate_plans(self.state_path)
        operations.accept_plan(self.state_path, plans[0]["id"])
        operations.import_handovers(self.state_path)
        audit = operations.audit_need(self.state_path, "N12")
        self.assertTrue(audit["reservations"])
        self.assertTrue(audit["statements"])
        self.assertTrue(audit["handovers"])
        self.assertTrue(audit["plans"])
        asset_audit = operations.audit_asset(self.state_path, "A003")
        self.assertFalse(asset_audit["committable"])
        self.assertEqual(asset_audit["flag"], "returned_pending_check")


if __name__ == "__main__":
    unittest.main()
