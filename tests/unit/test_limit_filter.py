"""LimitFilter 的纯函数部分测试。

核心打分逻辑需要 DB，放到 integration 测。这里只测辅助方法。
"""
from __future__ import annotations

import pytest

from mo_stock.filters.limit_filter import LimitFilter


class TestParseLimitTimes:
    """解析 Tushare up_stat 字段的测试。"""

    def test_none_or_empty_returns_1(self) -> None:
        assert LimitFilter._parse_limit_times(None) == 1
        assert LimitFilter._parse_limit_times("") == 1

    def test_normal_case(self) -> None:
        # "2/3" 表示近 3 次涨停中连板 2 次
        assert LimitFilter._parse_limit_times("2/3") == 2
        assert LimitFilter._parse_limit_times("5/10") == 5

    def test_invalid_format_falls_back(self) -> None:
        assert LimitFilter._parse_limit_times("abc") == 1


class TestFirstTimeBonus:
    """首次封板时间 → 加分映射。"""

    @pytest.mark.parametrize(
        ("first_time", "expected"),
        [
            ("09:30:00", 15),   # 开盘直接封板：最强
            ("09:55:00", 15),   # 10:00 前
            ("10:00:00", 15),   # 边界：10:00 整
            ("10:01:00", 10),   # 10:00 后
            ("10:59:00", 10),
            ("11:00:00", 10),   # 边界
            ("11:01:00", 5),
            ("13:30:00", 5),    # 边界：13:30 整
            ("13:31:00", 0),
            ("14:50:00", 0),    # 尾盘封板不加分
        ],
    )
    def test_time_bucket(self, first_time: str, expected: int) -> None:
        assert LimitFilter._first_time_bonus(first_time) == expected

    def test_invalid_format_returns_0(self) -> None:
        assert LimitFilter._first_time_bonus("bad") == 0
        assert LimitFilter._first_time_bonus("") == 0
