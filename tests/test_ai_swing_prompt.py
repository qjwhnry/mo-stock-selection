"""Swing AI prompt 构造测试。"""
from datetime import date

from mo_stock.ai.prompts import (
    build_methodology_prompt,
    build_swing_dynamic_stock_prompt,
    build_swing_methodology_prompt,
    build_swing_system_prompt,
    build_system_prompt,
)
from mo_stock.filters.base import ScoreResult


def test_swing_system_prompt_identity():
    p = build_swing_system_prompt()
    assert "波段" in p
    assert "5-20" in p
    assert "仅 JSON" in p


def test_short_system_prompt_unchanged():
    p = build_system_prompt()
    assert "短线" in p
    assert "1-3" in p


def test_swing_methodology_contains_7_dims():
    p = build_swing_methodology_prompt()
    for dim in ("trend", "pullback", "moneyflow_swing", "sector_swing",
                "theme_swing", "catalyst", "risk_liquidity"):
        assert dim in p, f"swing methodology 缺少维度 {dim}"
    assert "5-20" in p
    assert "MA10" in p or "MA20" in p


def test_short_methodology_unchanged():
    p = build_methodology_prompt()
    assert "limit" in p
    assert "1-3" in p


def test_swing_dynamic_contains_regime():
    scores = {
        "trend": ScoreResult(ts_code="000001.SZ", trade_date=date(2026, 4, 30), dim="trend", score=75, detail={"ma_status": "bullish"}),
    }
    p = build_swing_dynamic_stock_prompt(
        ts_code="000001.SZ",
        trade_date=date(2026, 4, 30),
        dim_scores=scores,
        regime_score=65.0,
        close=10.5,
        pct_chg=2.3,
        amount_yi=1.2,
    )
    assert "regime_score" in p
    assert "65.0" in p
    assert "trend" in p
    assert "000001.SZ" in p


def test_swing_dynamic_without_regime():
    p = build_swing_dynamic_stock_prompt(
        ts_code="000001.SZ",
        trade_date=date(2026, 4, 30),
        dim_scores={},
        regime_score=None,
        close=None,
        pct_chg=None,
        amount_yi=None,
    )
    assert "regime_score" in p
    assert "缺失" in p
