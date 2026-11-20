"""可替代判断规则的单元测试。"""
import datetime as dt
import unittest

from app import rules
from app.operations import load_dataset


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()

    def test_committable_states(self):
        ds = self.dataset
        ok, _ = rules.committable(ds, ds.assets["A020"], {})  # tested，有检测分
        self.assertTrue(ok)
        ok, reason = rules.committable(ds, ds.assets["A012"], {})  # quarantine
        self.assertFalse(ok)
        self.assertIn("待检测", reason)

    def test_unknown_condition_not_committable(self):
        ds = self.dataset
        # A018 闲置但检测得分未知：不能按可用来承诺
        ok, reason = rules.committable(ds, ds.assets["A018"], {})
        self.assertFalse(ok)
        self.assertIn("未知", reason)
        # A036 已检测状态但得分未知，同样不可承诺
        ok, _ = rules.committable(ds, ds.assets["A036"], {})
        self.assertFalse(ok)

    def test_handover_flag_overrides_state(self):
        ds = self.dataset
        ok, reason = rules.committable(
            ds, ds.assets["A020"], {"A020": "inspection_failed"}
        )
        self.assertFalse(ok)
        self.assertIn("不适用", reason)

    def test_release_day_includes_check_and_transport(self):
        ds = self.dataset
        # A001 占用至 09-05（含），返还检测 2 天 + 运输 1 天 → 09-09 才可供下一需求
        self.assertEqual(
            rules.release_day(ds, ds.assets["A001"]), dt.date(2026, 9, 9)
        )

    def test_ready_day_adds_repair_for_repairable(self):
        ds = self.dataset
        # A041 可修复：释放 09-13（占用结束后+检测运输），再加 1 天维修 → 09-14
        self.assertEqual(
            rules.ready_day(ds, ds.assets["A041"]), dt.date(2026, 9, 14)
        )
        # 非可修复资产的维修报价不计入到位日
        self.assertEqual(
            rules.ready_day(ds, ds.assets["A020"]), dt.date(2026, 9, 1)
        )

    def test_pair_status(self):
        ds = self.dataset
        index = rules.build_compat_index(ds.compats, dt.date(2026, 9, 22))
        status, ev = rules.pair_status(index, "desk", "M0", "M1")
        self.assertEqual(status, "confirmed")
        self.assertIsNotNone(ev)
        status, _ = rules.pair_status(index, "desk", "M0", "M0")
        self.assertEqual(status, "denied")
        # 未列配对为未知
        status, _ = rules.pair_status(index, "cooling", "M0", "M1")
        self.assertEqual(status, "unknown")

    def test_pair_status_respects_known_at(self):
        ds = self.dataset
        # 依据到达之前的评估日，配对结论不可得
        index = rules.build_compat_index(ds.compats, dt.date(2026, 8, 31))
        status, _ = rules.pair_status(index, "desk", "M0", "M1")
        self.assertEqual(status, "unknown")

    def test_suite_check(self):
        ds = self.dataset
        index = rules.build_compat_index(ds.compats, dt.date(2026, 9, 22))
        chosen = {
            "computer": [ds.assets["A020"]],  # M2
            "monitor": [ds.assets["A005"]],  # M5
            "adapter": [ds.assets["A010"]],  # M4
        }
        ok, problems, evidence = rules.suite_check(index, "desk", chosen)
        # M2×M5 确认兼容，M2×M4 已确认不兼容，M5×M4 已确认不兼容
        self.assertFalse(ok)
        self.assertTrue(any("不兼容" in p for p in problems))
        chosen_ok = {
            "computer": [ds.assets["A020"]],  # M2
            "monitor": [ds.assets["A005"]],  # M5
            "adapter": [ds.assets["A002"]],  # M2
        }
        ok, problems, evidence = rules.suite_check(index, "desk", chosen_ok)
        self.assertTrue(ok, problems)
        self.assertTrue(evidence)

    def test_effective_ready_day_pushes_past_reservation(self):
        ds = self.dataset
        asset = ds.assets["A020"]  # 原本 09-01 可交付
        need = ds.needs["N1"]
        reservations = [
            {
                "id": "RSV-X",
                "asset": "A020",
                "status": "active",
                "from_day": "2026-09-05",
                "until_release": "2026-10-02",
            }
        ]
        ready = rules.effective_ready_day(ds, asset, need, reservations)
        self.assertEqual(ready, dt.date(2026, 10, 3))
        # 已解除的预留不影响
        reservations[0]["status"] = "released"
        ready = rules.effective_ready_day(ds, asset, need, reservations)
        self.assertEqual(ready, dt.date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
