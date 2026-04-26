"""LhbFilter 单测（v2.1 plan Task 5：base 60 + seat 40 重排）。

v2.1 评分量纲：
- base 上限 60：base_score 20 + tier(0-20) + purity(0-12) + reason(0-8)
- seat 上限 40 / 下限 -15：institution / hot_money_buy / hot_money_sell / northbound

历史 0-100 base 量纲下的 detail 不会有 lhb_formula_version 字段；新口径恒为 2。
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.filters.lhb_filter import (
    LhbFilter,
    _is_drop_rebound_reason,
    _net_rate_tier_bonus,
    _purity_bonus,
    _reason_bonus,
    _seat_structure_score,
)
from mo_stock.storage.models import Lhb, LhbSeatDetail

# 默认 weights（与 v2.1 plan §3.4 一致）
DEFAULT_LHB_WEIGHTS = {
    "base_score": 20,
    "institution": {"min_net_buy_yuan": 10_000_000, "bonus": 20},
    "hot_money_buy": {"min_net_buy_yuan": 5_000_000, "bonus": 12},
    "hot_money_sell": {"min_net_sell_yuan": 10_000_000, "penalty": 15},
    "northbound_buy": {"min_net_buy_yuan": 30_000_000, "bonus": 8},
}


# 共享 sqlite_session（来自 conftest.py）
session = pytest.fixture(name="session")(lambda sqlite_session: sqlite_session)


# ============================================================================
# base 子项纯函数（v2.1 重排：上限 20/12/8）
# ============================================================================

class TestNetRateTierBonus:
    """v2.1 分档：2% → 10、5% → 15、10% → 20。"""

    @pytest.mark.parametrize(
        ("net_rate_pct", "expected"),
        [
            (-3.0, 0),       # 净卖出
            (0.0, 0),
            (1.5, 0),        # < 2% 太弱
            (2.0, 10),       # 边界 2%
            (3.36, 10),      # 赤天化 → 中等
            (4.99, 10),
            (5.0, 15),       # 边界 5%
            (8.0, 15),
            (10.0, 20),      # 边界 10% 满档
            (25.0, 20),      # 极端值封顶
        ],
    )
    def test_tier_thresholds(self, net_rate_pct: float, expected: int) -> None:
        assert _net_rate_tier_bonus(net_rate_pct) == expected

    def test_none_returns_zero(self) -> None:
        assert _net_rate_tier_bonus(None) == 0


class TestPurityBonus:
    """v2.1 分档：15% → 6、30% → 12。"""

    @pytest.mark.parametrize(
        ("amount_rate_pct", "expected"),
        [
            (-1.0, 0),
            (0.0, 0),
            (10.0, 0),
            (15.0, 6),       # 边界 15%
            (23.42, 6),      # 赤天化
            (29.99, 6),
            (30.0, 12),      # 边界 30% 满档
            (50.0, 12),
        ],
    )
    def test_purity_thresholds(self, amount_rate_pct: float, expected: int) -> None:
        assert _purity_bonus(amount_rate_pct) == expected

    def test_none_returns_zero(self) -> None:
        assert _purity_bonus(None) == 0


class TestReasonBonus:
    """v2.1 关键词权重：连续三日涨幅 8、其它涨幅类 5。"""

    def test_three_day_streak_strongest(self) -> None:
        assert _reason_bonus("连续三日涨幅偏离值达 20%") == 8

    def test_one_day_jump(self) -> None:
        assert _reason_bonus("日涨幅偏离值达 7%") == 5

    def test_high_turnover(self) -> None:
        assert _reason_bonus("日换手率达 20%") == 5

    def test_no_limit_security(self) -> None:
        assert _reason_bonus("无价格涨跌幅限制证券") == 5

    def test_drop_reasons_no_bonus(self) -> None:
        assert _reason_bonus("日跌幅偏离值达到 7%") == 0
        assert _reason_bonus("连续三日跌幅偏离值达 20%") == 0

    def test_unknown_reason(self) -> None:
        assert _reason_bonus("某种未识别的原因") == 0

    def test_none_or_empty(self) -> None:
        assert _reason_bonus(None) == 0
        assert _reason_bonus("") == 0

    def test_combo_reason_takes_max(self) -> None:
        # 多个原因用「、」拼接时取最高
        assert _reason_bonus("日涨幅偏离值达 7%、连续三日涨幅偏离值达 20%") == 8


class TestIsDropRebound:
    """跌幅榜识别（v2.1 不变）。"""

    @pytest.mark.parametrize("reason", [
        "日跌幅偏离值达 7%",
        "连续三日跌幅偏离值达 20%",
        "无价格涨跌幅限制日跌幅达 30%",
    ])
    def test_drop_reasons_detected(self, reason: str) -> None:
        assert _is_drop_rebound_reason(reason) is True

    @pytest.mark.parametrize("reason", [
        "日涨幅偏离值达 7%",
        "连续三日涨幅偏离值达 20%",
        "日换手率达 20%",
    ])
    def test_up_reasons_not_drop(self, reason: str) -> None:
        assert _is_drop_rebound_reason(reason) is False

    def test_none_or_empty(self) -> None:
        assert _is_drop_rebound_reason(None) is False
        assert _is_drop_rebound_reason("") is False


# ============================================================================
# seat 部分（v2.1 新增，上限 40 / 下限 -15）
# ============================================================================

def _seat(seat_no: int, seat_type: str, net_buy: float) -> LhbSeatDetail:
    """构造测试用 LhbSeatDetail 对象（不入库，仅供 _seat_structure_score 用）。"""
    return LhbSeatDetail(
        trade_date=date(2026, 4, 24), ts_code="600000.SH",
        seat_key=f"k{seat_no}", seat_no=seat_no,
        exalter=f"席位{seat_no}", side="0",
        buy=max(net_buy, 0), sell=max(-net_buy, 0), net_buy=net_buy,
        reason="测试", seat_type=seat_type,
    )


class TestSeatStructureScore:
    def test_no_seats_returns_zero(self) -> None:
        score, detail = _seat_structure_score([], DEFAULT_LHB_WEIGHTS)
        assert score == 0.0
        assert detail == {}

    def test_institution_buy_adds_20(self) -> None:
        seats = [_seat(1, "institution", 19_000_000)]
        score, detail = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 20.0
        assert detail["institution_net_buy"] == 19_000_000

    def test_institution_below_threshold_no_bonus(self) -> None:
        seats = [_seat(1, "institution", 5_000_000)]  # < 1000 万
        score, detail = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 0.0

    def test_hot_money_buy_adds_12(self) -> None:
        seats = [_seat(1, "hot_money", 8_000_000)]
        score, detail = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 12.0
        assert detail["hot_money_net_buy"] == 8_000_000

    def test_hot_money_sell_penalizes_15(self) -> None:
        seats = [_seat(1, "hot_money", -19_000_000)]  # 净卖 1900 万
        score, detail = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == -15.0
        assert detail["hot_money_sell_penalty"] == -15

    def test_northbound_buy_adds_8(self) -> None:
        seats = [_seat(1, "northbound", 35_000_000)]
        score, detail = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 8.0
        assert detail["northbound_net_buy"] == 35_000_000

    def test_combination_caps_at_40(self) -> None:
        """机构 +20、游资买 +12、北向 +8 = 40（恰好上限）。"""
        seats = [
            _seat(1, "institution", 20_000_000),
            _seat(2, "hot_money", 8_000_000),
            _seat(3, "northbound", 40_000_000),
        ]
        score, _ = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 40.0

    def test_other_type_ignored(self) -> None:
        """seat_type='other' 不参与加分。"""
        seats = [_seat(1, "other", 100_000_000)]
        score, _ = _seat_structure_score(seats, DEFAULT_LHB_WEIGHTS)
        assert score == 0.0


# ============================================================================
# score_all 端到端（用 sqlite_session 验证 base + seat 整合）
# ============================================================================

class TestLhbFilterScoreAll:
    def test_base_score_capped_at_60(self, session) -> None:
        """所有 base 子项打满 → 上限 60。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=12.0, amount_rate=35.0, reason="连续三日涨幅",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        results = f.score_all(session, td)
        score = next(r.score for r in results if r.ts_code == "600000.SH")
        # base = 20 + 20 + 12 + 8 = 60；无 seat → 60
        assert score == 60.0

    def test_lhb_formula_version_marked(self, session) -> None:
        """detail 含 lhb_formula_version=2，便于跟历史口径区分。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=3.0, amount_rate=15.0, reason="日涨幅偏离值达7%的证券",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        row = next(r for r in f.score_all(session, td) if r.ts_code == "600000.SH")
        assert row.detail["lhb_formula_version"] == 2

    def test_institution_buy_adds_seat_bonus(self, session) -> None:
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=3.0, amount_rate=15.0, reason="日涨幅偏离值达7%的证券",
        ))
        session.add(LhbSeatDetail(
            trade_date=td, ts_code="600000.SH", seat_key="k1", seat_no=1,
            exalter="机构专用", side="0",
            buy=20_000_000, sell=1_000_000, net_buy=19_000_000,
            reason="日涨幅偏离值达7%的证券", seat_type="institution",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        row = next(r for r in f.score_all(session, td) if r.ts_code == "600000.SH")
        # base = 20 + 10 + 6 + 5 = 41；seat = +20 → 61
        assert row.detail["institution_net_buy"] == 19_000_000
        assert row.score == 61.0

    def test_hot_money_sell_penalty(self, session) -> None:
        """知名游资大额净卖 → -15，最终分被压低。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600001.SH",
            net_rate=3.0, amount_rate=15.0, reason="日涨幅偏离值达7%的证券",
        ))
        session.add(LhbSeatDetail(
            trade_date=td, ts_code="600001.SH", seat_key="k1", seat_no=1,
            exalter="某知名游资营业部", side="0",
            buy=1_000_000, sell=20_000_000, net_buy=-19_000_000,
            reason="日涨幅偏离值达7%的证券", seat_type="hot_money",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        row = next(r for r in f.score_all(session, td) if r.ts_code == "600001.SH")
        # base = 41；seat = -15；最终 26
        assert row.detail["hot_money_sell_penalty"] == -15
        assert row.score == 26.0

    def test_no_seat_falls_back_to_base(self, session) -> None:
        """lhb_seat_detail 表为空时仍按 base 打分。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=3.0, amount_rate=15.0, reason="日涨幅偏离值达7%的证券",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        row = next(r for r in f.score_all(session, td) if r.ts_code == "600000.SH")
        # base = 20 + 10 + 6 + 5 = 41
        assert row.score == 41.0

    def test_drop_rebound_skipped(self, session) -> None:
        """跌幅榜上榜整股跳过。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=5.0, amount_rate=20.0, reason="日跌幅偏离值达7%的证券",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        results = f.score_all(session, td)
        assert all(r.ts_code != "600000.SH" for r in results)

    def test_net_rate_zero_or_negative_skipped(self, session) -> None:
        """net_rate ≤ 0 视为该维度信号缺失。"""
        td = date(2026, 4, 24)
        session.add(Lhb(
            trade_date=td, ts_code="600000.SH",
            net_rate=-1.0, amount_rate=15.0, reason="日涨幅偏离值达7%的证券",
        ))
        session.flush()

        f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
        results = f.score_all(session, td)
        assert all(r.ts_code != "600000.SH" for r in results)
