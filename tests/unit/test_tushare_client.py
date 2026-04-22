"""tushare_client 辅助工具与节流器测试。"""
from __future__ import annotations

import time
from datetime import date, datetime

from mo_stock.data_sources.tushare_client import (
    RateLimiter,
    date_to_tushare,
    tushare_to_date,
)


class TestDateConversions:
    def test_date_to_tushare(self) -> None:
        assert date_to_tushare(date(2026, 4, 22)) == "20260422"

    def test_datetime_to_tushare(self) -> None:
        assert date_to_tushare(datetime(2026, 4, 22, 15, 30)) == "20260422"

    def test_tushare_to_date(self) -> None:
        assert tushare_to_date("20260422") == date(2026, 4, 22)

    def test_roundtrip(self) -> None:
        d = date(2025, 12, 31)
        assert tushare_to_date(date_to_tushare(d)) == d


class TestRateLimiter:
    def test_first_call_not_blocked(self) -> None:
        """首次调用应该立即返回。"""
        limiter = RateLimiter(calls_per_minute=60)
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # 首次几乎不耗时

    def test_second_call_throttled(self) -> None:
        """第二次紧邻的调用应该被节流到最小间隔。"""
        # 120 次/分钟 → 每 0.5 秒一次
        limiter = RateLimiter(calls_per_minute=120)
        limiter.acquire()  # 占据时间基点

        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        # 允许少量偏差（调度抖动）
        assert 0.4 < elapsed < 0.6
