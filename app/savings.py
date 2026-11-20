"""节支认定口径与共享搬运分摊。

- 只有原本获准新购（procurement.approved）且实际被调剂替代的需求范围
  可以认定节支；暂缓或取消的需求（approved=false）不得认领。
- 可比新购价对应整组需求：不拆分需求未整组到齐不予认定，也不允许把
  comparable_cents 按随意比例分给已交部分；可拆分需求按已认定件数占
  整组必需件数的固定比例认定，口径写进说明。
- 净节支 = 可比新购价 × 认定比例 − 必要维修 − 必要交付安装费 − 共享
  搬运分摊。共享搬运整组只计一次；分摊到各需求时各方合计必须等于整组
  实际费用，依据写入分摊记录。
"""
from __future__ import annotations

from fractions import Fraction


def round_half_up(value: Fraction) -> int:
    """非负金额四舍五入到整数分。"""
    return int(value + Fraction(1, 2))


def recognized_fraction(need, scope: dict) -> tuple:
    """返回 (认定比例 Fraction, 口径说明)。scope 为 {组件: 已认定件数}。"""
    delivered = sum(scope.values())
    full = all(scope.get(kind, 0) >= qty for kind, qty in need.quantities.items())
    if full:
        return Fraction(1), "整组到齐，按整组认定"
    if not need.splittable:
        return Fraction(0), "不拆分需求未整组到齐，不予认定节支"
    return (
        Fraction(delivered, need.total_units),
        "可拆分需求按已认定件数占整组必需件数比例认定",
    )


def gross_cents(procurement, fraction: Fraction) -> int:
    return round_half_up(Fraction(procurement.comparable_cents) * fraction)


def allocate_shares(need_ids, trip_cents: int) -> dict:
    """组内均摊共享搬运费，余数按需求编号顺序分摊。

    返回 {需求id: 分摊分}，各方合计严格等于 trip_cents。
    """
    ids = sorted(need_ids)
    if not ids:
        return {}
    base, remainder = divmod(trip_cents, len(ids))
    return {
        nid: base + (1 if i < remainder else 0)
        for i, nid in enumerate(ids)
    }


def trip_basis_text(group: str, trip_cents: int, shares: dict) -> str:
    count = len(shares)
    return (
        f"共用搬运组 {group}：整组一次搬运费 {trip_cents} 分只计一次，"
        f"由组内 {count} 项认定需求均摊，余数按需求编号顺序分摊；"
        f"各方合计等于整组实际费用"
    )


DELIVERY_BASIS = "必要交付安装费按采购批准整组计列，该需求首次认定时计入"
REPAIR_BASIS = "仅计入可修复状态资产的已报价必要维修费"
