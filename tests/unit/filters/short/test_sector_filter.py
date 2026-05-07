"""SectorFilter 纯函数辅助测试。

主流程依赖 DB（join sw_daily + index_member），按现有 LimitFilter 模式不在
unit 层覆盖。这里只测易出 bug 的纯函数：板块涨幅 → rank → 加分映射。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from mo_stock.filters.short.sector_filter import (
    SectorFilter,
    _rank_to_bonus,
    _three_day_avg_bonus,
    _top_n_l1_codes,
)
from mo_stock.storage.models import DailyKline, IndexMember, SwDaily


class TestRankToBonus:
    """板块在当日涨幅榜的排名 → 加分。TOP 5 加分，之外不加。"""

    @pytest.mark.parametrize(
        ("rank", "expected"),
        [
            (1, 50),    # v2.4 降档后
            (2, 40),
            (3, 35),
            (4, 28),
            (5, 22),    # 第 5 名最低门槛
            (6, 0),     # 出 TOP 5 不加分
            (10, 0),
            (0, 0),     # 防御无效输入
        ],
    )
    def test_top5_decreasing(self, rank: int, expected: int) -> None:
        assert _rank_to_bonus(rank) == expected


class TestThreeDayAvgBonus:
    """板块近 3 日均涨幅（%）→ 加分。趋势加成。"""

    @pytest.mark.parametrize(
        ("avg_pct", "expected"),
        [
            (-1.0, 0),        # 近 3 日下跌
            (0.0, 0),
            (1.5, 0),         # 涨幅小不加
            (2.5, 10),        # v2.4 降档：> 2% 加 10
            (4.0, 10),
            (5.5, 20),        # v2.4 降档：> 5% 加 20（满档）
            (10.0, 20),
        ],
    )
    def test_thresholds(self, avg_pct: float, expected: int) -> None:
        assert _three_day_avg_bonus(avg_pct) == expected


class TestTopNL1Codes:
    """从 (sw_code, pct_change) 列表里取涨幅 TOP N 的 sw_code，返回 {sw_code: rank}。"""

    def test_basic_ordering(self) -> None:
        rows = [
            ("801080.SI", 5.5),   # 电子最强
            ("801120.SI", 3.2),   # 食品饮料
            ("801180.SI", -1.0),  # 房地产
            ("801200.SI", 4.8),   # 商贸零售
            ("801730.SI", 2.0),
            ("801770.SI", 1.5),
        ]
        result = _top_n_l1_codes(rows, n=3)
        # 前 3 名：电子(1) > 商贸零售(2) > 食品饮料(3)
        assert result == {
            "801080.SI": 1,
            "801200.SI": 2,
            "801120.SI": 3,
        }

    def test_skips_none_pct(self) -> None:
        rows = [
            ("801080.SI", None),   # 缺数据应被跳过
            ("801120.SI", 3.0),
            ("801200.SI", 4.0),
        ]
        result = _top_n_l1_codes(rows, n=2)
        assert result == {
            "801200.SI": 1,
            "801120.SI": 2,
        }

    def test_ties_stable_order(self) -> None:
        # 涨幅相同时按 sw_code 字典序保证确定性
        rows = [
            ("801200.SI", 3.0),
            ("801080.SI", 3.0),
        ]
        result = _top_n_l1_codes(rows, n=2)
        # 都进 TOP 2，sw_code 较小者排前
        assert result["801080.SI"] == 1
        assert result["801200.SI"] == 2

    def test_n_greater_than_input(self) -> None:
        rows = [("801080.SI", 5.0)]
        result = _top_n_l1_codes(rows, n=10)
        assert result == {"801080.SI": 1}


class TestSectorFilterScoring:
    def test_l2_strength_only_scores_industry_leaders(
        self, sqlite_session: Session,
    ) -> None:
        td = date(2026, 4, 24)
        strong_l2 = "801081.SI"
        weak_l2 = "801082.SI"
        sqlite_session.add(SwDaily(sw_code=strong_l2, trade_date=td, pct_change=5.0))
        sqlite_session.add(SwDaily(sw_code=weak_l2, trade_date=td, pct_change=1.0))

        for i in range(6):
            code = f"60000{i}.SH"
            sqlite_session.add(IndexMember(ts_code=code, l2_code=strong_l2))
            sqlite_session.add(DailyKline(
                ts_code=code,
                trade_date=td,
                pct_chg=6.0 - i,
                amount=6000.0 - i * 1000,
                vol=1000.0,
            ))
        sqlite_session.flush()

        f = SectorFilter(weights={"top_n_l2": 1})
        rows = f.score_all(sqlite_session, td)
        by_code = {r.ts_code: r for r in rows}

        assert by_code["600000.SH"].score == 100.0
        assert by_code["600001.SH"].score == 100.0
        assert by_code["600002.SH"].score == 100.0
        assert "600003.SH" not in by_code

    def test_weak_l2_without_leadership_signal_returns_empty(
        self, sqlite_session: Session,
    ) -> None:
        """弱势 L2 不再输出负分；没有强势领涨确认就不写 sector 维度。"""
        td = date(2026, 4, 24)
        strong_l2 = "801081.SI"
        weak_l2 = "801099.SI"
        sqlite_session.add(SwDaily(sw_code=strong_l2, trade_date=td, pct_change=3.0))
        sqlite_session.add(SwDaily(sw_code=weak_l2, trade_date=td, pct_change=-5.0))
        sqlite_session.add(IndexMember(ts_code="600099.SH", l2_code=strong_l2))
        sqlite_session.add(DailyKline(
            ts_code="600099.SH",
            trade_date=td,
            pct_chg=2.0,
            amount=1000.0,
            vol=1000.0,
        ))

        pct_values = [-3.0, -2.0, -1.0, 0.0, 0.5, 1.0]
        for i, pct in enumerate(pct_values):
            code = f"60001{i}.SH"
            sqlite_session.add(IndexMember(ts_code=code, l2_code=weak_l2))
            sqlite_session.add(DailyKline(
                ts_code=code,
                trade_date=td,
                pct_chg=pct,
                amount=1000.0 + i,
                vol=1000.0,
            ))
        sqlite_session.flush()

        f = SectorFilter(weights={"top_n_l2": 1})
        rows = f.score_all(sqlite_session, td)
        by_code = {r.ts_code: r for r in rows}

        assert "600010.SH" not in by_code
        assert "600011.SH" not in by_code
        assert "600012.SH" not in by_code
