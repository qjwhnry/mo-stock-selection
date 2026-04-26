"""tushare_client 辅助工具与节流器测试。"""
from __future__ import annotations

import time
from datetime import date, datetime

import pandas as pd

from mo_stock.data_sources.tushare_client import (
    RateLimiter,
    TushareClient,
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


# ---------------------------------------------------------------------------
# Task 1（v2.1 plan）：5 个新增接口的方法 dispatch 测试
# 仅验证 client 方法把正确的 api_name + 关键 fields 传给 _call，
# 不真调网络（避免单测依赖外部 API 与 token）。
# ---------------------------------------------------------------------------


def _patched_client(monkeypatch) -> tuple[TushareClient, list]:
    """构造一个绕过 _init 的 client，monkeypatch 掉 _call 收集调用记录。"""
    client = TushareClient.__new__(TushareClient)
    calls: list = []

    def fake_call(api_name, **kwargs):
        calls.append((api_name, kwargs))
        return pd.DataFrame()

    monkeypatch.setattr(client, "_call", fake_call)
    return client, calls


class TestNewClientMethods:
    def test_ths_daily_calls_tushare_api(self, monkeypatch) -> None:
        client, calls = _patched_client(monkeypatch)
        client.ths_daily(trade_date="20260424")
        assert calls[0][0] == "ths_daily"
        assert calls[0][1]["trade_date"] == "20260424"
        # 表 + 评分逻辑都依赖这些字段，必须在 fields 里
        assert "pct_change" in calls[0][1]["fields"]
        assert "turnover_rate" in calls[0][1]["fields"]

    def test_limit_cpt_list_calls_tushare_api(self, monkeypatch) -> None:
        client, calls = _patched_client(monkeypatch)
        client.limit_cpt_list(trade_date="20260424")
        assert calls[0][0] == "limit_cpt_list"
        assert calls[0][1]["trade_date"] == "20260424"
        assert "up_nums" in calls[0][1]["fields"]
        assert "rank" in calls[0][1]["fields"]

    def test_moneyflow_cnt_ths_calls_tushare_api(self, monkeypatch) -> None:
        """v2 修法：3 个净额字段都要拿，与 ThsConceptMoneyflow 表对齐。"""
        client, calls = _patched_client(monkeypatch)
        client.moneyflow_cnt_ths(trade_date="20260424")
        assert calls[0][0] == "moneyflow_cnt_ths"
        assert calls[0][1]["trade_date"] == "20260424"
        assert "net_amount" in calls[0][1]["fields"]
        assert "net_buy_amount" in calls[0][1]["fields"]
        assert "net_sell_amount" in calls[0][1]["fields"]

    def test_hm_list_calls_tushare_api(self, monkeypatch) -> None:
        client, calls = _patched_client(monkeypatch)
        client.hm_list()
        assert calls[0][0] == "hm_list"
        assert "name" in calls[0][1]["fields"]
        assert "orgs" in calls[0][1]["fields"]

    def test_hm_detail_calls_tushare_api(self, monkeypatch) -> None:
        client, calls = _patched_client(monkeypatch)
        client.hm_detail(trade_date="20260424")
        assert calls[0][0] == "hm_detail"
        assert calls[0][1]["trade_date"] == "20260424"
        assert "hm_name" in calls[0][1]["fields"]
        assert "net_amount" in calls[0][1]["fields"]
