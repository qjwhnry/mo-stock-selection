"""LimitFilter 的纯函数部分测试。

核心打分逻辑需要 DB，放到 integration 测。这里只测辅助方法。
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.filters.short.limit_context import parse_limit_times
from mo_stock.filters.short.limit_filter import (
    LimitFilter,
    _break_board_rebound_bonus,
    _limit_up_ratio,
    _near_limit_up_fade_bonus,
    _near_limit_up_threshold,
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


class TestParseLimitTimesStandalone:
    """limit_context.parse_limit_times 独立函数测试。"""

    @pytest.mark.parametrize(
        ("limit_times", "up_stat", "expected"),
        [
            (1, None, 1),
            (2, "2/3", 2),
            (None, "3/5", 3),
            (None, None, 1),
            (None, "abc", 1),
            (0, "1/1", 1),   # 0 is falsy, fallback to up_stat
        ],
    )
    def test_various_inputs(
        self, limit_times: int | None, up_stat: str | None, expected: int,
    ) -> None:
        assert parse_limit_times(limit_times, up_stat) == expected


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
    """断板反包：昨涨停今没涨停但今天保持强势。

    v2.5 增加 board_position 连板位置感知。
    """

    @pytest.mark.parametrize(
        ("yesterday_was_limit_up", "today_is_limit_up", "today_pct_chg", "expected"),
        [
            # 不满足条件的：
            (False, False, 5.0, 0),   # 昨没涨停 → 不算反包
            (True, True, 9.5, 0),     # 今天又涨停了 → 不是断板，是连板
            (True, False, -2.0, 0),   # 今跌 → 不是反包
            (True, False, 0.5, 0),    # 涨幅太小 < 1% 不算反包
            # 满足条件（首板后断板，board_position=1，兼容旧行为）：
            (True, False, 1.5, 30),   # 1~3% 弱反包
            (True, False, 3.0, 50),   # 3~5% 中反包
            (True, False, 5.5, 70),   # 5~8% 强反包
            (True, False, 9.0, 100),  # ≥8% 极强反包
            # None 边界
            (True, False, None, 0),
        ],
    )
    def test_thresholds_board_position_1(
        self,
        yesterday_was_limit_up: bool,
        today_is_limit_up: bool,
        today_pct_chg: float | None,
        expected: int,
    ) -> None:
        assert _break_board_rebound_bonus(
            yesterday_was_limit_up, today_is_limit_up, today_pct_chg,
        ) == expected

    def test_second_board_rebound_penalized(self) -> None:
        """二连板后断板涨 5% → 50（原 70）。"""
        assert _break_board_rebound_bonus(
            True, False, 5.27, board_position=2,
        ) == 50

    def test_third_board_rebound_capped(self) -> None:
        """三连板以上断板涨 5% → 30（原 70）。"""
        assert _break_board_rebound_bonus(
            True, False, 5.27, board_position=3,
        ) == 30

    def test_non_rebound_zero(self) -> None:
        """昨未涨停，不触发断板反包。"""
        assert _break_board_rebound_bonus(
            False, False, 5.0, board_position=1,
        ) == 0

    def test_today_limit_up_not_rebound(self) -> None:
        """今日继续涨停 = 连板非断板。"""
        assert _break_board_rebound_bonus(
            True, True, 10.0, board_position=1,
        ) == 0

    @pytest.mark.parametrize(
        ("today_pct_chg", "board_position", "expected"),
        [
            # board_position=2 各档
            (9.0, 2, 80),
            (5.5, 2, 50),
            (3.5, 2, 30),
            (1.5, 2, 15),
            # board_position=3 各档
            (9.0, 3, 40),
            (5.5, 3, 30),
            (3.5, 3, 20),
            (1.5, 3, 10),
        ],
    )
    def test_all_board_position_tiers(
        self, today_pct_chg: float, board_position: int, expected: int,
    ) -> None:
        assert _break_board_rebound_bonus(
            True, False, today_pct_chg, board_position=board_position,
        ) == expected


class TestDynamicLimitUpRatio:
    def test_main_board_10cm(self) -> None:
        assert _limit_up_ratio("002158.SZ") == 1.10
        assert _limit_up_ratio("600519.SH") == 1.10

    def test_gem_20cm(self) -> None:
        assert _limit_up_ratio("300750.SZ") == 1.20
        assert _limit_up_ratio("301001.SZ") == 1.20

    def test_star_market_20cm(self) -> None:
        assert _limit_up_ratio("688981.SH") == 1.20

    def test_star_market_689_20cm(self) -> None:
        """689xxx 也是科创板，20cm。"""
        assert _limit_up_ratio("689001.SH") == 1.20

    def test_bse_30cm(self) -> None:
        """北交所 30cm 涨停。"""
        assert _limit_up_ratio("830001.BJ") == 1.30
        assert _limit_up_ratio("430001.BJ") == 1.30

    def test_near_limit_threshold(self) -> None:
        """10cm 涨停价 pre*1.10, 阈值 pre*1.10*0.99"""
        threshold = _near_limit_up_threshold(10.0, 1.10)
        assert threshold == pytest.approx(10.0 * 1.10 * 0.99)

        threshold_20cm = _near_limit_up_threshold(10.0, 1.20)
        assert threshold_20cm == pytest.approx(10.0 * 1.20 * 0.99)

        threshold_30cm = _near_limit_up_threshold(10.0, 1.30)
        assert threshold_30cm == pytest.approx(10.0 * 1.30 * 0.99)


class TestNearLimitUpFade:
    """全天收盘位置判断（close_position = (close - low) / (high - low)）。"""

    def test_true_fade_close_in_lower_half(self) -> None:
        """近涨停 + 收盘在下半区（close_position=0.29）→ -20。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=29.5, today_close=30.5,
            pre_close=30.0,
        ) == -20

    def test_hanzhong_no_trigger(self) -> None:
        """汉钟精机 5/7 真实数据：close_position=0.572 > 0.5 → 不触发。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.33, today_low=30.15,
            today_close=31.97, pre_close=30.37,
        ) == 0

    def test_strong_uptrend_no_penalty(self) -> None:
        """近涨停 + 收盘在上半区（close_position=0.86）→ 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="600519.SH",
            today_high=33.0, today_low=29.8, today_close=32.5,
            pre_close=29.5,
        ) == 0

    def test_doji_fade_triggered(self) -> None:
        """十字星冲高回落（close_position≈0.2）→ 仍然触发。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=29.0, today_close=29.8,
            pre_close=30.0,
        ) == -20

    def test_far_from_limit_no_penalty(self) -> None:
        """距涨停很远 → 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=31.5, today_low=29.0, today_close=29.3,
            pre_close=29.5,
        ) == 0

    def test_20cm_stock_near_limit(self) -> None:
        """20cm 股票用 1.20 阈值。"""
        assert _near_limit_up_fade_bonus(
            ts_code="300750.SZ",
            today_high=35.8, today_low=29.0, today_close=30.2,
            pre_close=30.0,
        ) == -20  # 35.8 >= 35.64, close_position=0.17

    def test_one_line_limit_no_penalty(self) -> None:
        """一字板 day_range=0 → 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=33.0, today_close=33.0,
            pre_close=30.0,
        ) == 0

    def test_none_fields_return_zero(self) -> None:
        """任一 None 字段 → 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=None, today_low=30.0, today_close=31.0, pre_close=30.0,
        ) == 0


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
        # 首板断板，board_position 应为 1
        assert rebound.detail["board_position"] == 1

    def test_second_board_rebound_gets_lower_score(self, sqlite_session) -> None:
        """二连板后断板 → board_position=2 → 分数低于首板断板。"""
        prev_day = date(2026, 4, 24)
        today = date(2026, 4, 27)
        ts_code = "000001.SZ"

        sqlite_session.add_all([
            TradeCal(cal_date=prev_day, is_open=True, pretrade_date=date(2026, 4, 23)),
            TradeCal(cal_date=today, is_open=True, pretrade_date=prev_day),
            StockBasic(
                ts_code=ts_code,
                symbol="000001",
                name="平安银行",
                industry="银行",
                sw_l1="银行",
                list_date=date(1991, 4, 3),
                is_st=False,
            ),
            # 二连板
            LimitList(
                ts_code=ts_code,
                trade_date=prev_day,
                limit_type="U",
                fd_amount=50_000_000.0,
                first_time="10:00:00",
                last_time="14:55:00",
                open_times=0,
                up_stat="2/2",
                limit_times=2,
            ),
            DailyKline(
                ts_code=ts_code,
                trade_date=today,
                open=10.0, high=11.0, low=10.0, close=10.9,
                pre_close=10.0, pct_chg=9.0, vol=100_000.0, amount=100_000.0,
            ),
        ])
        sqlite_session.commit()

        results = LimitFilter(weights={}).score_all(sqlite_session, today)
        rebound = next(r for r in results if r.ts_code == ts_code and r.score > 0)

        # 二连板断板涨 9% → board_position=2 → bonus=80（不是 100）
        assert rebound.score == 80
        assert rebound.detail["board_position"] == 2
