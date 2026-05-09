"""exhaustion 维度准入与透传的端到端语义测试。

验证 3 条核心不变量：
1. exhaustion-only 不入 selection_result（不被当作准入信号）
2. moneyflow + exhaustion=0 仍准入（正向催化 + 严重透支风险共存时保留）
3. analyzer admission 状态正确反映准入判断
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import JSON


def _register_jsonb_on_sqlite() -> None:
    from sqlalchemy.dialects.sqlite import base as sqlite_base

    if not hasattr(sqlite_base.SQLiteTypeCompiler, "visit_JSONB"):
        def visit_JSONB(self, type_, **kw):  # noqa: N802
            return self.process(JSON(), **kw)

        sqlite_base.SQLiteTypeCompiler.visit_JSONB = visit_JSONB


@pytest.fixture
def session_for_admission():
    """构造测试数据：
    - EXH_ONLY.SH: 仅有 exhaustion=80 的打分（无正向催化）
    - MF_PLUS_EXH0.SH: moneyflow=40 + exhaustion=0（有催化但严重透支）
    - MF_HEALTHY.SH: moneyflow=50 + exhaustion=95（正常参考股）
    """
    _register_jsonb_on_sqlite()

    from mo_stock.storage.models import (
        Base,
        DailyKline,
        FilterScoreDaily,
        StockBasic,
    )

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
    session = Session()

    trade_date = date(2026, 5, 9)

    for code in ("EXH_ONLY.SH", "MF_PLUS_EXH0.SH", "MF_HEALTHY.SH"):
        session.add(StockBasic(
            ts_code=code,
            symbol=code.split(".")[0],
            name=f"测试{code[:3]}",
            is_st=False,
            list_date=date(2020, 1, 1),
        ))
        session.add(DailyKline(
            ts_code=code,
            trade_date=trade_date,
            open=10.0,
            close=10.5,
            pct_chg=5.0,
            amount=500000.0,
        ))

    # EXH_ONLY: 只有 exhaustion 打分，无正向催化
    session.add(FilterScoreDaily(
        trade_date=trade_date,
        strategy="short",
        ts_code="EXH_ONLY.SH",
        dim="exhaustion",
        score=80.0,
        detail={"freshness_score": 80.0},
    ))

    # MF_PLUS_EXH0: moneyflow 正分 + exhaustion=0（严重透支）
    session.add(FilterScoreDaily(
        trade_date=trade_date,
        strategy="short",
        ts_code="MF_PLUS_EXH0.SH",
        dim="moneyflow",
        score=50.0,
        detail={"net_mf_amount": 5000},
    ))
    session.add(FilterScoreDaily(
        trade_date=trade_date,
        strategy="short",
        ts_code="MF_PLUS_EXH0.SH",
        dim="exhaustion",
        score=0.0,
        detail={"freshness_score": 0.0, "penalty_5d_return": 35.0},
    ))

    # MF_HEALTHY: moneyflow 正分 + exhaustion 高分（正常）
    session.add(FilterScoreDaily(
        trade_date=trade_date,
        strategy="short",
        ts_code="MF_HEALTHY.SH",
        dim="moneyflow",
        score=50.0,
        detail={"net_mf_amount": 5000},
    ))
    session.add(FilterScoreDaily(
        trade_date=trade_date,
        strategy="short",
        ts_code="MF_HEALTHY.SH",
        dim="exhaustion",
        score=95.0,
        detail={"freshness_score": 95.0},
    ))

    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def session_for_analyzer():
    """为 analyzer 测试构造完整数据：Moneyflow 表 + 多日 K 线 + 交易日历。

    - NO_CATALYST.SH: 无正向催化（exhaustion 高分但无 moneyflow 等）
    - HAS_MF.SH: 有 moneyflow 正向催化（net_mf_amount > 0 且 close > open）
    """
    _register_jsonb_on_sqlite()

    from mo_stock.storage.models import (
        Base,
        DailyKline,
        Moneyflow,
        StockBasic,
        TradeCal,
    )

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
    session = Session()

    trade_date = date(2026, 5, 9)

    # 11 个交易日（满足 exhaustion 的 6 日最低要求）
    cal_dates = [trade_date - timedelta(days=i) for i in range(11)]
    for d in cal_dates:
        session.add(TradeCal(cal_date=d, is_open=True))

    for code in ("NO_CATALYST.SH", "HAS_MF.SH"):
        session.add(StockBasic(
            ts_code=code,
            symbol=code.split(".")[0],
            name=f"测试{code[:3]}",
            is_st=False,
            list_date=date(2020, 1, 1),
        ))
        # 多日 K 线（exhaustion 需要 6 天以上）
        for i, d in enumerate(reversed(cal_dates)):
            session.add(DailyKline(
                ts_code=code,
                trade_date=d,
                open=10.0 + i * 0.05,
                close=10.0 + i * 0.1,
                pct_chg=1.0,
                amount=500000.0,
            ))

    # HAS_MF: 正向资金流
    session.add(Moneyflow(
        ts_code="HAS_MF.SH",
        trade_date=trade_date,
        net_mf_amount=8000,  # 万元
        buy_sm_amount=1000, sell_sm_amount=1200,
        buy_md_amount=3000, sell_md_amount=2000,
        buy_lg_amount=10000, sell_lg_amount=3000,
        buy_elg_amount=5000, sell_elg_amount=800,
    ))

    session.commit()
    yield session
    session.close()
    engine.dispose()


class TestExhaustionAdmission:
    """exhaustion 维度准入不变量。"""

    def test_exhaustion_only_not_in_selection(self, session_for_admission):
        """仅有 exhaustion 打分的股票不应进入 selection_result。"""
        from mo_stock.scorer.combine import combine_scores

        dim_weights = {
            "limit": 0.25, "moneyflow": 0.25, "lhb": 0.20,
            "sector": 0.10, "theme": 0.10, "exhaustion": 0.10,
        }
        hard_reject_cfg = {"exclude_st": False, "min_list_days": 0}

        combine_scores(
            session_for_admission,
            date(2026, 5, 9),
            dim_weights,
            hard_reject_cfg,
            top_n=20,
            enable_ai=False,
            strategy="short",
        )

        from mo_stock.storage.models import SelectionResult
        results = session_for_admission.execute(
            __import__("sqlalchemy").select(SelectionResult)
            .where(SelectionResult.trade_date == date(2026, 5, 9))
            .where(SelectionResult.strategy == "short")
        ).scalars().all()

        codes_in_result = {r.ts_code for r in results}
        assert "EXH_ONLY.SH" not in codes_in_result, (
            "exhaustion-only 股票不应进入 selection_result"
        )

    def test_moneyflow_plus_exhaustion_zero_still_admitted(self, session_for_admission):
        """有正向催化（moneyflow>0）但 exhaustion=0 的股票仍应被准入。"""
        from mo_stock.scorer.combine import combine_scores

        dim_weights = {
            "limit": 0.25, "moneyflow": 0.25, "lhb": 0.20,
            "sector": 0.10, "theme": 0.10, "exhaustion": 0.10,
        }
        hard_reject_cfg = {"exclude_st": False, "min_list_days": 0}

        combine_scores(
            session_for_admission,
            date(2026, 5, 9),
            dim_weights,
            hard_reject_cfg,
            top_n=20,
            enable_ai=False,
            strategy="short",
        )

        from mo_stock.storage.models import SelectionResult
        results = session_for_admission.execute(
            __import__("sqlalchemy").select(SelectionResult)
            .where(SelectionResult.trade_date == date(2026, 5, 9))
            .where(SelectionResult.strategy == "short")
            .where(SelectionResult.ts_code == "MF_PLUS_EXH0.SH")
        ).scalars().all()

        assert len(results) > 0, "moneyflow+exhaustion=0 股票应出现在 selection_result"
        assert results[0].picked, "有正向催化的股票应被 picked"

    def test_exhaustion_score_modifies_ranking(self, session_for_admission):
        """exhaustion 分数应改变排序：正常股 > 透支股（同 moneyflow 水平下）。"""
        from mo_stock.scorer.combine import combine_scores

        dim_weights = {
            "limit": 0.25, "moneyflow": 0.25, "lhb": 0.20,
            "sector": 0.10, "theme": 0.10, "exhaustion": 0.10,
        }
        hard_reject_cfg = {"exclude_st": False, "min_list_days": 0}

        combine_scores(
            session_for_admission,
            date(2026, 5, 9),
            dim_weights,
            hard_reject_cfg,
            top_n=20,
            enable_ai=False,
            strategy="short",
        )

        from mo_stock.storage.models import SelectionResult
        results = session_for_admission.execute(
            __import__("sqlalchemy").select(SelectionResult)
            .where(SelectionResult.trade_date == date(2026, 5, 9))
            .where(SelectionResult.strategy == "short")
            .where(SelectionResult.picked.is_(True))
        ).scalars().all()

        by_code = {r.ts_code: r for r in results}
        assert "MF_HEALTHY.SH" in by_code, "健康股应被 picked"
        assert "MF_PLUS_EXH0.SH" in by_code, "透支股应被 picked"

        healthy = by_code["MF_HEALTHY.SH"]
        exhausted = by_code["MF_PLUS_EXH0.SH"]
        assert healthy.rule_score > exhausted.rule_score, (
            f"同 moneyflow 下，正常股 rule_score ({healthy.rule_score}) "
            f"应高于透支股 ({exhausted.rule_score})"
        )


class TestAnalyzerAdmission:
    """analyzer 的 admitted 字段验证。"""

    def test_no_catalyst_not_admitted(self, session_for_analyzer):
        """analyzer 对无正向催化的股票应返回 admitted=False。"""
        from mo_stock.analyzer import analyze_stock

        result = analyze_stock(
            session_for_analyzer,
            "NO_CATALYST.SH",
            date(2026, 5, 9),
        )
        assert not result["admitted"], "无正向催化的股票不应被准入"

    def test_moneyflow_stock_is_admitted(self, session_for_analyzer):
        """analyzer 对有 moneyflow 催化的股票应返回 admitted=True。"""
        from mo_stock.analyzer import analyze_stock

        result = analyze_stock(
            session_for_analyzer,
            "HAS_MF.SH",
            date(2026, 5, 9),
        )
        assert result["admitted"], "有 moneyflow 催化的股票应被准入"
