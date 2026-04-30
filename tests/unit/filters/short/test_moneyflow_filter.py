"""MoneyflowFilter 纯函数辅助测试。

主流程依赖 DB（moneyflow + daily_kline join），按现有项目模式只测纯函数。
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter, _today_bonus_tier
from mo_stock.storage.models import DailyKline, Moneyflow, TradeCal


class TestTodayBonusTier:
    """主力净流入占当日成交额比例 → 加分。

    单位换算：
      net_mf_amount 单位「万元」，daily_kline.amount 单位「千元」
      ratio (%) = 1000 × net_mf_wan / amount_qy

    经验阈值（业界量化研究通用）：
      ≥ 5%   极强主动建仓 → +25
      1~5%   强主动力度    → +18
      0.3~1% 中主动力度    → +10
      0~0.3% 弱信号        → +3（赤天化 0.157% 在此段）
    """

    @pytest.mark.parametrize(
        ("net_mf_wan", "amount_qy", "expected"),
        [
            # 赤天化真实例：221.16 万元 / 1408139 千元 → 0.157% → +5
            (221.16, 1_408_139.0, 5),
            # 边界：0.3% 中档
            (300.0, 1_000_000.0, 20),     # 1000 * 300 / 1_000_000 = 0.3% 边界 → +20
            # 1% 边界 → 强档
            (1000.0, 1_000_000.0, 35),    # 1.0% → +35
            # 5% 边界 → 极强档（满档）
            (5000.0, 1_000_000.0, 50),    # 5.0% → +50
            (10000.0, 1_000_000.0, 50),   # 10% 也是 +50（满档）
            # 0.5% 中等
            (500.0, 1_000_000.0, 20),
            # 净流出
            (-100.0, 1_000_000.0, 0),
            (0.0, 1_000_000.0, 0),
        ],
    )
    def test_tier_thresholds(
        self, net_mf_wan: float, amount_qy: float, expected: int,
    ) -> None:
        assert _today_bonus_tier(net_mf_wan, amount_qy) == expected

    def test_none_or_zero_amount_returns_zero(self) -> None:
        # 当日成交额缺失 → 无法算占比，给 0
        assert _today_bonus_tier(1000.0, None) == 0
        assert _today_bonus_tier(1000.0, 0.0) == 0
        assert _today_bonus_tier(None, 1000.0) == 0


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
        assert results[0].score == 55
        assert results[0].detail["rolling_3d_wan"] == 900.0
        assert results[0].detail["rolling_bonus"] == 20
