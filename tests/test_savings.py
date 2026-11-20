"""节支认定口径的单元测试。"""
import unittest
from fractions import Fraction

from app import savings
from app.operations import load_dataset


class SavingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()

    def test_non_splittable_partial_recognizes_nothing(self):
        need = self.dataset.needs["N1"]  # 不拆分
        fraction, basis = savings.recognized_fraction(
            need, {"computer": 2, "monitor": 2}  # 缺关键配件 adapter
        )
        self.assertEqual(fraction, 0)
        self.assertIn("不拆分", basis)

    def test_splittable_partial_uses_unit_share(self):
        need = self.dataset.needs["N4"]  # 可拆分，整组 6 件
        fraction, basis = savings.recognized_fraction(
            need, {"computer": 2, "monitor": 2}
        )
        self.assertEqual(fraction, Fraction(4, 6))
        self.assertIn("比例", basis)

    def test_full_scope_recognizes_whole(self):
        need = self.dataset.needs["N1"]
        fraction, _ = savings.recognized_fraction(
            need, {"computer": 2, "monitor": 2, "adapter": 2}
        )
        self.assertEqual(fraction, 1)

    def test_allocate_shares_sums_to_trip(self):
        shares = savings.allocate_shares(["N1", "N4", "N7", "N13"], 15000)
        self.assertEqual(sum(shares.values()), 15000)
        self.assertTrue(all(v == 3750 for v in shares.values()))

    def test_allocate_shares_remainder_deterministic(self):
        shares = savings.allocate_shares(["N3", "N1", "N2"], 100)
        self.assertEqual(sum(shares.values()), 100)
        # 余数按需求编号顺序分摊
        self.assertEqual(shares, {"N1": 34, "N2": 33, "N3": 33})

    def test_allocate_empty_group(self):
        self.assertEqual(savings.allocate_shares([], 15000), {})

    def test_gross_rounding_half_up(self):
        proc = self.dataset.procurement_of("N4")  # 184000 分
        gross = savings.gross_cents(proc, Fraction(2, 3))
        self.assertEqual(gross, 122667)  # 122666.67 四舍五入到分


if __name__ == "__main__":
    unittest.main()
