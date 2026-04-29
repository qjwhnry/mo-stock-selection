"""mo_stock.analyzer 单股分析测试。"""
from __future__ import annotations

from datetime import date

from mo_stock.analyzer import analyze_stock
from mo_stock.filters.base import ScoreResult
from mo_stock.storage.models import StockBasic


def test_analyze_stock_includes_theme_filter(sqlite_session, monkeypatch) -> None:
    """单股分析应与 run-once 一样包含 ThemeFilter。"""
    ts_code = "000001.SZ"
    trade_date = date(2026, 4, 24)
    sqlite_session.add(StockBasic(
        ts_code=ts_code,
        symbol="000001",
        name="平安银行",
        industry="银行",
        sw_l1="银行",
        list_date=date(1991, 4, 3),
        is_st=False,
    ))
    sqlite_session.commit()

    monkeypatch.setattr("mo_stock.filters.limit_filter.LimitFilter.score_all", lambda self, s, d: [])
    monkeypatch.setattr("mo_stock.filters.moneyflow_filter.MoneyflowFilter.score_all", lambda self, s, d: [])
    monkeypatch.setattr("mo_stock.filters.lhb_filter.LhbFilter.score_all", lambda self, s, d: [])
    monkeypatch.setattr("mo_stock.filters.sector_filter.SectorFilter.score_all", lambda self, s, d: [])
    monkeypatch.setattr(
        "mo_stock.filters.theme_filter.ThemeFilter.score_all",
        lambda self, s, d: [
            ScoreResult(
                ts_code=ts_code,
                trade_date=trade_date,
                dim="theme",
                score=80.0,
                detail={"best_concept": "885001.TI"},
            )
        ],
    )

    result = analyze_stock(
        sqlite_session,
        ts_code,
        trade_date,
        weights_cfg={
            "dimension_weights": {"theme": 0.10},
            "hard_reject": {"exclude_st": False, "min_list_days": 0},
        },
    )

    assert result["dimensions"]["theme"]["score"] == 80.0
    assert result["rule_score"] == 80.0
