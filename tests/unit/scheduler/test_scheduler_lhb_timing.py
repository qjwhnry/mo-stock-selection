"""调度层时点保护单测（P0-1）。

龙虎榜数据由交易所于 T 日 ~15:30 后发布。若调度被运维误提前到盘中
（如 14:00），LhbFilter 会用"还没出"的龙虎榜信号 → look-ahead bias。

`_assert_lhb_data_available` 仅在 trade_date == 当日 时校验，
回填历史日期不触发。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from mo_stock.scheduler.daily_job import CN_TZ, _assert_lhb_data_available, _build_filters


class TestAssertLhbDataAvailable:
    def test_after_1530_passes(self) -> None:
        """当日 15:30 后调用，正常通过。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 15, 31, tzinfo=CN_TZ)
        _assert_lhb_data_available(today, now=now)  # 不抛

    def test_before_1530_blocks(self) -> None:
        """当日 15:30 前调用，立即 RuntimeError 阻止运行。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 14, 0, tzinfo=CN_TZ)
        with pytest.raises(RuntimeError, match="早于龙虎榜发布时间"):
            _assert_lhb_data_available(today, now=now)

    def test_at_exactly_1530_passes(self) -> None:
        """边界：15:30:00 整点视为已发布（与 cron 触发时刻一致）。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 15, 30, tzinfo=CN_TZ)
        _assert_lhb_data_available(today, now=now)  # 不抛

    def test_historical_backfill_not_checked(self) -> None:
        """回填历史日期：盘中调用也允许（数据早就出了）。"""
        yesterday = date.today() - timedelta(days=1)
        # 即便用"今天 10:00"作为 now，trade_date 是昨天 → 不校验时点
        now = datetime.now(CN_TZ).replace(hour=10, minute=0, second=0, microsecond=0)
        _assert_lhb_data_available(yesterday, now=now)  # 不抛


def test_scheduler_builds_swing_filters_and_dims() -> None:
    filters, dims = _build_filters("swing", {})

    assert dims == [
        "trend", "pullback", "moneyflow_swing", "sector_swing",
        "theme_swing", "catalyst", "risk_liquidity",
    ]
    assert [f.dim for f in filters] == dims
