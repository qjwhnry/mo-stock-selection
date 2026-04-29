"""LimitFilter 的纯函数部分测试。

核心打分逻辑需要 DB，放到 integration 测。这里只测辅助方法。
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.filters.limit_filter import (
    LimitFilter,
    _break_board_rebound_bonus,
)
from mo_stock.storage.models import DailyKline, LimitList, StockBasic, TradeCal


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


class TestBreakBoardReboundBonus:
    """断板反包：昨涨停今没涨停但今天保持强势 → 给基础分 + 涨幅梯度。

    PLAN.md 指定的核心场景：「首板 > 连板首日 > 断板反包」。
    断板反包股 hard_reject 不会过滤（今天非涨停），是 limit 维度真正能进 TOP 的来源。
    """

    @pytest.mark.parametrize(
        ("yesterday_was_limit_up", "today_is_limit_up", "today_pct_chg", "expected"),
        [
            # 不满足条件的：
            (False, False, 5.0, 0),   # 昨没涨停 → 不算反包
            (True, True, 9.5, 0),     # 今天又涨停了 → 不是断板，是连板（由 LimitFilter 主流程处理）
            (True, False, -2.0, 0),   # 今跌 → 不是反包
            (True, False, 0.5, 0),    # 涨幅太小 < 1% 不算反包
            # 满足条件：
            (True, False, 1.5, 30),   # 1~3% 弱反包 → 基础 30
            (True, False, 3.0, 50),   # 3~5% 中反包 → 基础 30 + 20
            (True, False, 5.5, 70),   # 5~8% 强反包 → 基础 30 + 40
            (True, False, 9.0, 100),  # ≥8% 极强反包 → 满档 100
            # None 边界
            (True, False, None, 0),
        ],
    )
    def test_thresholds(
        self,
        yesterday_was_limit_up: bool,
        today_is_limit_up: bool,
        today_pct_chg: float | None,
        expected: int,
    ) -> None:
        assert _break_board_rebound_bonus(
            yesterday_was_limit_up, today_is_limit_up, today_pct_chg,
        ) == expected


# v2.3 起移除 sector_heat_bonus（与 sector 维度共线性 → 板块全员霸榜）。
# 见 docs/audit-sector-concentration-2026-04-28.md。


class TestLimitFilterTradingDay:
    """断板反包必须使用上一交易日，而不是自然日前一天。"""

    def test_monday_uses_friday_limit_up_for_rebound(self, sqlite_session) -> None:
        friday = date(2026, 4, 24)
        monday = date(2026, 4, 27)
        ts_code = "000001.SZ"

        sqlite_session.add_all([
            TradeCal(cal_date=friday, is_open=True, pretrade_date=date(2026, 4, 23)),
            TradeCal(cal_date=monday, is_open=True, pretrade_date=friday),
            StockBasic(
                ts_code=ts_code,
                symbol="000001",
                name="平安银行",
                industry="银行",
                sw_l1="银行",
                list_date=date(1991, 4, 3),
                is_st=False,
            ),
            LimitList(
                ts_code=ts_code,
                trade_date=friday,
                limit_type="U",
                fd_amount=100_000_000.0,
                first_time="09:45:00",
                last_time="14:55:00",
                open_times=0,
                up_stat="1/1",
                limit_times=1,
            ),
            DailyKline(
                ts_code=ts_code,
                trade_date=monday,
                open=10.0,
                high=10.8,
                low=10.0,
                close=10.7,
                pre_close=10.0,
                pct_chg=7.0,
                vol=100_000.0,
                amount=100_000.0,
            ),
        ])
        sqlite_session.commit()

        results = LimitFilter(weights={}).score_all(sqlite_session, monday)

        rebound = next(r for r in results if r.ts_code == ts_code and r.score > 0)
        assert rebound.score == 70
        assert rebound.detail["yesterday_limit_up"] is True
