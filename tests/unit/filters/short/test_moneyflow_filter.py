"""MoneyflowFilter 纯函数辅助测试。

主流程依赖 DB（moneyflow + daily_kline join），按现有项目模式只测纯函数。
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter, _today_bonus_tier
from mo_stock.storage.models import DailyKline, Moneyflow, TradeCal


class TestTodayBonusTier:
    """主力净流入占当日成交额比例 → 连续加分（v2.4 线性插值）。

    单位换算：
      net_mf_amount 单位「万元」，daily_kline.amount 单位「千元」
      ratio (%) = 1000 × net_mf_wan / amount_qy

    连续公式：ratio < 0.3% → 0; 0.3%~5% → 线性 5~50; ≥5% → 50。
    """

    @pytest.mark.parametrize(
        ("net_mf_wan", "amount_qy", "expected"),
        [
            # 赤天化真实例：0.157% < 0.3% → 0
            (221.16, 1_408_139.0, 0),
            # 边界：0.3% → 5.0（线性起点）
            (300.0, 1_000_000.0, 5.0),
            # 1.0% → 线性中间值 ≈ 10.96
            (1000.0, 1_000_000.0, pytest.approx(5.0 + 0.7 / 4.7 * 40, abs=0.1)),
            # 5% 边界 → 封顶 50
            (5000.0, 1_000_000.0, 50.0),
            # 10% 也封顶 50
            (10000.0, 1_000_000.0, 50.0),
            # 净流出 → 0
            (-100.0, 1_000_000.0, 0),
            (0.0, 1_000_000.0, 0),
        ],
    )
    def test_continuous_values(
        self, net_mf_wan: float, amount_qy: float, expected: float,
    ) -> None:
        assert _today_bonus_tier(net_mf_wan, amount_qy) == expected

    def test_none_or_zero_amount_returns_zero(self) -> None:
        assert _today_bonus_tier(1000.0, None) == 0
        assert _today_bonus_tier(1000.0, 0.0) == 0
        assert _today_bonus_tier(None, 1000.0) == 0

    def test_monotonic_increasing(self) -> None:
        """连续函数在 0.3%~5% 区间单调递增。"""
        amounts = [350, 500, 1000, 2000, 3500, 5000]
        scores = [_today_bonus_tier(a, 1_000_000.0) for a in amounts]
        for i in range(1, len(scores)):
            assert scores[i] > scores[i - 1], (
                f"非单调：{amounts[i-1]}→{scores[i-1]}, {amounts[i]}→{scores[i]}"
            )


class TestMoneyflowFilterScoring:
    def test_rolling_sum_uses_batch_query_result(self, sqlite_session) -> None:
        """近 3 日累计净流入来自批量聚合结果，正值触发 rolling_bonus。"""
        d1 = date(2026, 4, 22)
        d2 = date(2026, 4, 23)
        d3 = date(2026, 4, 24)
        ts_code = "000001.SZ"

        sqlite_session.add_all([
            TradeCal(cal_date=d1, is_open=True, pretrade_date=date(2026, 4, 21)),
            TradeCal(cal_date=d2, is_open=True, pretrade_date=d1),
            TradeCal(cal_date=d3, is_open=True, pretrade_date=d2),
            DailyKline(
                ts_code=ts_code,
                trade_date=d3,
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                pre_close=10.0,
                pct_chg=2.0,
                vol=100_000.0,
                amount=1_000_000.0,
            ),
            Moneyflow(
                ts_code=ts_code,
                trade_date=d1,
                net_mf_amount=-100.0,
                buy_sm_amount=0.0,
                sell_sm_amount=0.0,
                buy_md_amount=0.0,
                sell_md_amount=0.0,
                buy_lg_amount=0.0,
                sell_lg_amount=0.0,
                buy_elg_amount=0.0,
                sell_elg_amount=0.0,
            ),
            Moneyflow(
                ts_code=ts_code,
                trade_date=d3,
                net_mf_amount=1_000.0,
                buy_sm_amount=0.0,
                sell_sm_amount=0.0,
                buy_md_amount=0.0,
                sell_md_amount=0.0,
                buy_lg_amount=0.0,
                sell_lg_amount=0.0,
                buy_elg_amount=0.0,
                sell_elg_amount=0.0,
            ),
        ])
        sqlite_session.commit()

        results = MoneyflowFilter(weights={"rolling_3d_bonus": 20}).score_all(
            sqlite_session, d3,
        )

        assert len(results) == 1
        # v2.4 连续打分：1.0% → 5 + 0.7/4.7*40 ≈ 10.96 + rolling 20 ≈ 30.96
        assert results[0].score == pytest.approx(30.96, abs=0.1)
        assert results[0].detail["rolling_3d_wan"] == 900.0
        assert results[0].detail["rolling_bonus"] == 20

    def test_weak_inflow_no_other_signals_excluded(self, sqlite_session) -> None:
        """净流入 < 0.3% 且无 rolling/ratio 正信号 → score=0 → 不入 results。"""
        d1 = date(2026, 4, 22)
        d2 = date(2026, 4, 23)
        d3 = date(2026, 4, 24)
        ts_code = "000002.SZ"

        sqlite_session.add_all([
            TradeCal(cal_date=d1, is_open=True, pretrade_date=date(2026, 4, 21)),
            TradeCal(cal_date=d2, is_open=True, pretrade_date=d1),
            TradeCal(cal_date=d3, is_open=True, pretrade_date=d2),
            DailyKline(
                ts_code=ts_code,
                trade_date=d3,
                open=10.0, high=10.5, low=9.8, close=10.2,
                pre_close=10.0, pct_chg=0.5, vol=100_000.0, amount=1_000_000.0,
            ),
            # d1 大额净流出 → rolling_sum = -500 + 10 = -490 < 0（无 rolling_bonus）
            Moneyflow(
                ts_code=ts_code,
                trade_date=d1,
                net_mf_amount=-500.0,
                buy_sm_amount=0.0, sell_sm_amount=0.0,
                buy_md_amount=0.0, sell_md_amount=0.0,
                buy_lg_amount=0.0, sell_lg_amount=0.0,
                buy_elg_amount=0.0, sell_elg_amount=0.0,
            ),
            # d3 微正净流入：10 万 / 1M 千元 = 0.01% < 0.3%（today_bonus=0）
            Moneyflow(
                ts_code=ts_code,
                trade_date=d3,
                net_mf_amount=10.0,
                buy_sm_amount=0.0, sell_sm_amount=0.0,
                buy_md_amount=0.0, sell_md_amount=0.0,
                buy_lg_amount=0.0, sell_lg_amount=0.0,
                buy_elg_amount=0.0, sell_elg_amount=0.0,
            ),
        ])
        sqlite_session.commit()

        results = MoneyflowFilter(weights={"rolling_3d_bonus": 20}).score_all(
            sqlite_session, d3,
        )
        assert len(results) == 0
