"""ThemeFilter 单测（v2.1 plan Task 4）。

ThemeFilter 与 sector 平级，从 ths_daily / limit_concept_daily /
ths_concept_moneyflow 三类信号合成，0-100 分。

核心约定：多概念股**取最高概念加分**，不累加（避免沾边股霸榜）。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from mo_stock.filters.theme_filter import ThemeFilter, _bonus_from_table
from mo_stock.storage.models import (
    LimitConceptDaily,
    ThsConceptMoneyflow,
    ThsDaily,
    ThsMember,
)

# 默认 weights（与 v2.1 plan §3.3 一致）
DEFAULT_THEME_WEIGHTS = {
    "top_n_themes": 10,
    "ths_rank_bonus": {1: 50, 2: 42, 3: 35, 4: 28, 5: 22, 10: 12},
    "limit_concept_rank_bonus": {1: 50, 3: 35, 5: 22, 10: 12},
    "concept_moneyflow_positive_bonus": 15,
    "max_theme_bonus": 100,
}


# 共享 sqlite_session（来自 conftest.py，已 patch JSONB → JSON）
session = pytest.fixture(name="session")(lambda sqlite_session: sqlite_session)


class TestBonusFromTable:
    """_bonus_from_table：找 ≥ rank 的最小 key 对应分数。"""

    def test_exact_rank(self) -> None:
        table = {1: 50, 2: 42, 5: 22}
        assert _bonus_from_table(table, 1) == 50
        assert _bonus_from_table(table, 5) == 22

    def test_between_keys(self) -> None:
        """rank=4 在表 {1:50, 2:42, 5:22} 中 → 取 5 对应分数（保守）。"""
        table = {1: 50, 2: 42, 5: 22}
        assert _bonus_from_table(table, 4) == 22

    def test_rank_zero_or_negative(self) -> None:
        assert _bonus_from_table({1: 50}, 0) == 0
        assert _bonus_from_table({1: 50}, -1) == 0

    def test_rank_exceeds_max_key(self) -> None:
        """rank 超出 max key → 0（不在 TOP N 范围）。"""
        assert _bonus_from_table({1: 50, 5: 22}, 6) == 0

    def test_empty_table(self) -> None:
        assert _bonus_from_table({}, 1) == 0


class TestThemeFilterScoring:
    def test_no_theme_data_returns_empty(self, session: Session) -> None:
        """三类信号全空时返回空列表。"""
        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        assert f.score_all(session, date(2026, 4, 24)) == []

    def test_full_signal_caps_at_100(self, session: Session) -> None:
        """概念排第 1 + 涨停最强排第 1 + 资金净流入 → max bonus 100（封顶）。"""
        td = date(2026, 4, 24)
        # ths_daily 唯一 1 条 → rank 1
        session.add(ThsDaily(ts_code="885806.TI", trade_date=td, name="华为", pct_change=8.0))
        session.add(LimitConceptDaily(ts_code="885806.TI", trade_date=td, name="华为", rank=1))
        session.add(ThsConceptMoneyflow(ts_code="885806.TI", trade_date=td, name="华为", net_amount=10.5))
        session.add(ThsMember(ts_code="885806.TI", con_code="600000.SH", con_name="浦发"))
        session.flush()

        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        results = f.score_all(session, td)
        by_code = {r.ts_code: r for r in results}
        # 50 (ths rank 1) + 50 (limit rank 1) + 15 (moneyflow > 0) = 115 → clamp 100
        assert by_code["600000.SH"].score == 100.0
        assert by_code["600000.SH"].detail["best_concept"] == "885806.TI"

    def test_multi_concept_takes_max(self, session: Session) -> None:
        """多概念股取最高概念加分，不累加。"""
        td = date(2026, 4, 24)
        # 概念 A: ths rank 1 + limit rank 1（强）
        session.add(ThsDaily(ts_code="885806.TI", trade_date=td, pct_change=8.0))
        session.add(LimitConceptDaily(ts_code="885806.TI", trade_date=td, rank=1))
        # 概念 B: ths rank 5（中）
        session.add(ThsDaily(ts_code="885900.TI", trade_date=td, pct_change=3.0))
        # 同一只股属于两个概念
        session.add(ThsMember(ts_code="885806.TI", con_code="600000.SH"))
        session.add(ThsMember(ts_code="885900.TI", con_code="600000.SH"))
        session.flush()

        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        score = next(r.score for r in f.score_all(session, td) if r.ts_code == "600000.SH")
        # 概念 A: 50+50+0=100；概念 B: 22+0+0=22 → max 100
        assert score == 100.0

    def test_limit_concept_only(self, session: Session) -> None:
        """没在 ths_daily 但在涨停最强榜的概念也算分（渐进降级）。"""
        td = date(2026, 4, 24)
        session.add(LimitConceptDaily(ts_code="885950.TI", trade_date=td, rank=3))
        session.add(ThsMember(ts_code="885950.TI", con_code="600001.SH"))
        session.flush()

        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        results = f.score_all(session, td)
        score = next(r.score for r in results if r.ts_code == "600001.SH")
        # 仅 limit_concept rank 3 → 35
        assert score == 35.0

    def test_theme_continues_when_ths_daily_empty(self, session: Session) -> None:
        """v2.1 关键设计：ths_daily 完全为空时仍跑 limit_concept + moneyflow 信号。"""
        td = date(2026, 4, 24)
        # 没有 ths_daily，只有 limit_concept + moneyflow
        session.add(LimitConceptDaily(ts_code="885950.TI", trade_date=td, rank=1))
        session.add(ThsConceptMoneyflow(ts_code="885950.TI", trade_date=td, net_amount=5.0))
        session.add(ThsMember(ts_code="885950.TI", con_code="600002.SH"))
        session.flush()

        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        results = f.score_all(session, td)
        score = next(r.score for r in results if r.ts_code == "600002.SH")
        # limit rank 1 (50) + moneyflow > 0 (15) = 65
        assert score == 65.0

    def test_moneyflow_negative_no_bonus(self, session: Session) -> None:
        """net_amount ≤ 0 → 不加 moneyflow_bonus。"""
        td = date(2026, 4, 24)
        session.add(ThsDaily(ts_code="885806.TI", trade_date=td, pct_change=5.0))
        session.add(ThsConceptMoneyflow(ts_code="885806.TI", trade_date=td, net_amount=-2.0))
        session.add(ThsMember(ts_code="885806.TI", con_code="600000.SH"))
        session.flush()

        f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
        score = next(r.score for r in f.score_all(session, td) if r.ts_code == "600000.SH")
        # ths rank 1 (50) + 0 + 0 = 50（无 moneyflow_bonus）
        assert score == 50.0
