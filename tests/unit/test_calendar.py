"""data_sources/calendar.py 纯函数测试。"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.data_sources.calendar import classify_market, is_selectable
from mo_stock.storage.models import StockBasic


class TestClassifyMarket:
    """ts_code → 板块归属。"""

    @pytest.mark.parametrize(
        ("ts_code", "expected"),
        [
            ("600519.SH", "主板-沪"),
            ("601318.SH", "主板-沪"),
            ("603986.SH", "主板-沪"),
            ("000001.SZ", "主板-深"),
            ("002594.SZ", "主板-深"),
            ("300750.SZ", "创业板"),
            ("301239.SZ", "创业板"),
            ("688981.SH", "科创板"),
            ("831010.BJ", "北交所"),
            ("430047.BJ", "北交所"),
        ],
    )
    def test_various_prefixes(self, ts_code: str, expected: str) -> None:
        assert classify_market(ts_code) == expected


class TestIsSelectable:
    """股票是否可选入候选池。"""

    def _make_basic(
        self,
        name: str = "贵州茅台",
        is_st: bool = False,
        list_date: date | None = date(2001, 8, 27),
    ) -> StockBasic:
        """构造测试用 StockBasic（未入库，纯内存对象）。"""
        b = StockBasic()
        b.ts_code = "600519.SH"
        b.symbol = "600519"
        b.name = name
        b.is_st = is_st
        b.list_date = list_date
        return b

    def test_normal_stock_is_selectable(self, sample_trade_date: date) -> None:
        basic = self._make_basic()
        ok, reason = is_selectable(basic, sample_trade_date)
        assert ok is True
        assert reason == ""

    def test_st_stock_rejected_by_flag(self, sample_trade_date: date) -> None:
        basic = self._make_basic(name="ST 中安", is_st=True)
        ok, reason = is_selectable(basic, sample_trade_date)
        assert ok is False
        assert "ST" in reason

    def test_st_stock_rejected_by_name(self, sample_trade_date: date) -> None:
        # is_st=False 但名称含 ST 也要拦截
        basic = self._make_basic(name="*ST 中安", is_st=False)
        ok, reason = is_selectable(basic, sample_trade_date)
        assert ok is False
        assert "ST" in reason

    def test_newly_listed_rejected(self, sample_trade_date: date) -> None:
        # 距离 trade_date 不足 60 天的次新
        basic = self._make_basic(list_date=date(2026, 3, 1))  # 距 4/22 仅 52 天
        ok, reason = is_selectable(basic, sample_trade_date, min_list_days=60)
        assert ok is False
        assert "上市仅" in reason

    def test_boundary_60_days_passes(self, sample_trade_date: date) -> None:
        # 恰好 60 天
        basic = self._make_basic(list_date=date(2026, 2, 21))  # 距 4/22 正好 60 天
        ok, _ = is_selectable(basic, sample_trade_date, min_list_days=60)
        assert ok is True
