"""LhbFilter 纯函数辅助测试。

主流程依赖 DB（lhb 表读取），按现有 LimitFilter / MoneyflowFilter 的项目模式
不在 unit 层覆盖，只测易出 bug 的纯函数。
"""
from __future__ import annotations

import pytest

from mo_stock.filters.lhb_filter import (
    _is_drop_rebound_reason,
    _net_rate_tier_bonus,
    _purity_bonus,
    _reason_bonus,
)


class TestNetRateTierBonus:
    """龙虎榜净买入占当日总成交比例（%）→ 加分。Tushare 现成字段 net_rate。

    跨股可比：大盘股净额 5 亿但占比 0.5% 弱；小盘股 1000 万占比 8% 强。
    """

    @pytest.mark.parametrize(
        ("net_rate_pct", "expected"),
        [
            (-3.0, 0),       # 净卖出（占比为负）
            (0.0, 0),        # 持平
            (1.5, 0),        # < 2% 阈值（信号太弱）
            (2.0, 10),       # 边界 2%
            (3.36, 10),      # 赤天化真实值 → 信号中等
            (4.99, 10),
            (5.0, 15),       # 边界 5%
            (8.0, 15),
            (10.0, 20),      # 边界 10%
            (25.0, 20),      # 极端值封顶
        ],
    )
    def test_tier_thresholds(self, net_rate_pct: float, expected: int) -> None:
        assert _net_rate_tier_bonus(net_rate_pct) == expected

    def test_none_returns_zero(self) -> None:
        assert _net_rate_tier_bonus(None) == 0


class TestPurityBonus:
    """龙虎榜成交占当日总成交比例（%）→ 加分。Tushare 现成字段 amount_rate。"""

    @pytest.mark.parametrize(
        ("amount_rate_pct", "expected"),
        [
            (-1.0, 0),        # 异常负值
            (0.0, 0),
            (10.0, 0),        # < 15% 阈值
            (15.0, 10),       # 边界 15%
            (23.42, 10),      # 赤天化真实值 → 中等
            (29.99, 10),
            (30.0, 20),       # 边界 30%
            (50.0, 20),
        ],
    )
    def test_purity_thresholds(self, amount_rate_pct: float, expected: int) -> None:
        assert _purity_bonus(amount_rate_pct) == expected

    def test_none_returns_zero(self) -> None:
        assert _purity_bonus(None) == 0


class TestReasonBonus:
    """上榜原因文本 → 加分。**仅涨幅类**加分；跌幅类由 _is_drop_rebound_reason 单独识别。"""

    def test_three_day_streak_strongest(self) -> None:
        assert _reason_bonus("连续三日涨幅偏离值达 20%") == 10

    def test_one_day_jump(self) -> None:
        assert _reason_bonus("日涨幅偏离值达 7%") == 5

    def test_high_turnover(self) -> None:
        assert _reason_bonus("日换手率达 20%") == 5

    def test_no_limit_security(self) -> None:
        assert _reason_bonus("无价格涨跌幅限制证券") == 5

    def test_drop_reasons_no_longer_bonus(self) -> None:
        # 跌幅类不再加分（之前给 5/10，跌停反弹策略跟"找强势股"目标矛盾）
        assert _reason_bonus("日跌幅偏离值达到 7%") == 0
        assert _reason_bonus("连续三日跌幅偏离值达 20%") == 0

    def test_unknown_reason(self) -> None:
        assert _reason_bonus("某种未识别的原因") == 0

    def test_none_or_empty(self) -> None:
        assert _reason_bonus(None) == 0
        assert _reason_bonus("") == 0

    def test_combo_reason_takes_max(self) -> None:
        # 多个原因用「、」拼接时，取最高分（不叠加避免双重计分）
        assert _reason_bonus("日涨幅偏离值达 7%、连续三日涨幅偏离值达 20%") == 10


class TestIsDropRebound:
    """识别「跌幅榜上榜」reason，整股直接出 LhbFilter（避免跌停反弹股入选）。"""

    @pytest.mark.parametrize(
        "reason",
        [
            "日跌幅偏离值达 7%",
            "连续三日跌幅偏离值达 20%",
            "日跌幅偏离值达到 7%的前 5 只证券",
            "无价格涨跌幅限制日跌幅达 30%",
        ],
    )
    def test_drop_reasons_detected(self, reason: str) -> None:
        assert _is_drop_rebound_reason(reason) is True

    @pytest.mark.parametrize(
        "reason",
        [
            "日涨幅偏离值达 7%",
            "连续三日涨幅偏离值达 20%",
            "日换手率达 20%",
            "无价格涨跌幅限制证券",
        ],
    )
    def test_up_reasons_not_drop(self, reason: str) -> None:
        assert _is_drop_rebound_reason(reason) is False

    def test_none_or_empty(self) -> None:
        assert _is_drop_rebound_reason(None) is False
        assert _is_drop_rebound_reason("") is False
