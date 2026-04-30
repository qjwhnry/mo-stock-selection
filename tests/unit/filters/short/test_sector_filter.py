"""SectorFilter 纯函数辅助测试。

主流程依赖 DB（join sw_daily + index_member），按现有 LimitFilter 模式不在
unit 层覆盖。这里只测易出 bug 的纯函数：板块涨幅 → rank → 加分映射。
"""
from __future__ import annotations

import pytest

from mo_stock.filters.short.sector_filter import (
    _rank_to_bonus,
    _three_day_avg_bonus,
    _top_n_l1_codes,
)


class TestRankToBonus:
    """板块在当日涨幅榜的排名 → 加分。TOP 5 加分，之外不加。"""

    @pytest.mark.parametrize(
        ("rank", "expected"),
        [
            (1, 50),    # v2.4 降档后
            (2, 40),
            (3, 35),
            (4, 28),
            (5, 22),    # 第 5 名最低门槛
            (6, 0),     # 出 TOP 5 不加分
            (10, 0),
            (0, 0),     # 防御无效输入
        ],
    )
    def test_top5_decreasing(self, rank: int, expected: int) -> None:
        assert _rank_to_bonus(rank) == expected


class TestThreeDayAvgBonus:
    """板块近 3 日均涨幅（%）→ 加分。趋势加成。"""

    @pytest.mark.parametrize(
        ("avg_pct", "expected"),
        [
            (-1.0, 0),        # 近 3 日下跌
            (0.0, 0),
            (1.5, 0),         # 涨幅小不加
            (2.5, 10),        # v2.4 降档：> 2% 加 10
            (4.0, 10),
            (5.5, 20),        # v2.4 降档：> 5% 加 20（满档）
            (10.0, 20),
        ],
    )
    def test_thresholds(self, avg_pct: float, expected: int) -> None:
        assert _three_day_avg_bonus(avg_pct) == expected


class TestTopNL1Codes:
    """从 (sw_code, pct_change) 列表里取涨幅 TOP N 的 sw_code，返回 {sw_code: rank}。"""

    def test_basic_ordering(self) -> None:
        rows = [
            ("801080.SI", 5.5),   # 电子最强
            ("801120.SI", 3.2),   # 食品饮料
            ("801180.SI", -1.0),  # 房地产
            ("801200.SI", 4.8),   # 商贸零售
            ("801730.SI", 2.0),
            ("801770.SI", 1.5),
        ]
        result = _top_n_l1_codes(rows, n=3)
        # 前 3 名：电子(1) > 商贸零售(2) > 食品饮料(3)
        assert result == {
            "801080.SI": 1,
            "801200.SI": 2,
            "801120.SI": 3,
        }

    def test_skips_none_pct(self) -> None:
        rows = [
            ("801080.SI", None),   # 缺数据应被跳过
            ("801120.SI", 3.0),
            ("801200.SI", 4.0),
        ]
        result = _top_n_l1_codes(rows, n=2)
        assert result == {
            "801200.SI": 1,
            "801120.SI": 2,
        }

    def test_ties_stable_order(self) -> None:
        # 涨幅相同时按 sw_code 字典序保证确定性
        rows = [
            ("801200.SI", 3.0),
            ("801080.SI", 3.0),
        ]
        result = _top_n_l1_codes(rows, n=2)
        # 都进 TOP 2，sw_code 较小者排前
        assert result["801080.SI"] == 1
        assert result["801200.SI"] == 2

    def test_n_greater_than_input(self) -> None:
        rows = [("801080.SI", 5.0)]
        result = _top_n_l1_codes(rows, n=10)
        assert result == {"801080.SI": 1}
