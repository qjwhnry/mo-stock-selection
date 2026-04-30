"""swing Phase 1 基础规则测试。"""
from __future__ import annotations

from datetime import date, timedelta

from mo_stock.filters.catalyst_filter import CatalystFilter
from mo_stock.filters.market_regime_filter import MarketRegimeFilter
from mo_stock.filters.trend_filter import TrendFilter
from mo_stock.scorer.combine import combine_scores
from mo_stock.storage.models import (
    DailyKline,
    FilterScoreDaily,
    LimitList,
    SelectionResult,
    StockBasic,
    TradeCal,
)


def _seed_trade_cal(sqlite_session, start: date, days: int) -> list[date]:
    dates = [start + timedelta(days=i) for i in range(days)]
    for d in dates:
        sqlite_session.add(TradeCal(cal_date=d, is_open=True, pretrade_date=d - timedelta(days=1)))
    sqlite_session.commit()
    return dates


def test_trend_filter_scores_bullish_stock(sqlite_session) -> None:
    dates = _seed_trade_cal(sqlite_session, date(2026, 1, 1), 70)
    sqlite_session.add(StockBasic(
        ts_code="600001.SH",
        symbol="600001",
        name="测试股份",
        area=None,
        industry=None,
        sw_l1=None,
        list_date=date(2020, 1, 1),
        is_st=False,
    ))
    for i, d in enumerate(dates):
        close = 100 + i * 0.8
        sqlite_session.add(DailyKline(
            ts_code="600001.SH",
            trade_date=d,
            open=close - 0.2,
            high=close + 0.5,
            low=close - 0.8,
            close=close,
            pre_close=close - 0.8,
            pct_chg=0.8,
            vol=1000 + i * 20,
            amount=100000.0,
        ))
    sqlite_session.commit()

    results = TrendFilter().score_all(sqlite_session, dates[-1])

    assert len(results) == 1
    assert results[0].ts_code == "600001.SH"
    assert results[0].dim == "trend"
    assert results[0].score > 0
    assert results[0].detail["ma_bullish"] is True


def test_market_regime_missing_index_degrades_to_neutral(sqlite_session) -> None:
    score = MarketRegimeFilter().score_market(sqlite_session, date(2026, 4, 30))

    assert score == 50.0


def test_catalyst_filter_scores_break_board_rebound_without_limit_filter(sqlite_session) -> None:
    prev = date(2026, 4, 29)
    td = date(2026, 4, 30)
    sqlite_session.add_all([
        TradeCal(cal_date=prev, is_open=True, pretrade_date=date(2026, 4, 28)),
        TradeCal(cal_date=td, is_open=True, pretrade_date=prev),
        StockBasic(
            ts_code="600001.SH",
            symbol="600001",
            name="反包股份",
            area=None,
            industry=None,
            sw_l1=None,
            list_date=date(2020, 1, 1),
            is_st=False,
        ),
        LimitList(
            ts_code="600001.SH",
            trade_date=prev,
            limit_type="U",
            fd_amount=None,
            first_time=None,
            last_time=None,
            open_times=None,
            up_stat=None,
            limit_times=None,
        ),
        DailyKline(
            ts_code="600001.SH",
            trade_date=td,
            open=10,
            high=10.8,
            low=9.9,
            close=10.6,
            pre_close=10,
            pct_chg=6.0,
            vol=1000,
            amount=100000,
        ),
    ])
    sqlite_session.commit()

    results = CatalystFilter().score_all(sqlite_session, td)

    assert len(results) == 1
    assert results[0].ts_code == "600001.SH"
    assert results[0].dim == "catalyst"
    assert results[0].detail["break_board_rebound"] == 70


def test_combine_scores_applies_market_regime_top_n(sqlite_session) -> None:
    td = date(2026, 4, 30)
    _seed_trade_cal(sqlite_session, td, 1)
    for ts_code, name in [("600001.SH", "一号"), ("600002.SH", "二号")]:
        sqlite_session.add(StockBasic(
            ts_code=ts_code,
            symbol=ts_code[:6],
            name=name,
            area=None,
            industry=None,
            sw_l1=None,
            list_date=date(2020, 1, 1),
            is_st=False,
        ))
        sqlite_session.add(DailyKline(
            ts_code=ts_code,
            trade_date=td,
            open=10,
            high=11,
            low=9,
            close=10,
            pre_close=10,
            pct_chg=0,
            vol=1000,
            amount=100000,
        ))
    sqlite_session.add_all([
        FilterScoreDaily(
            trade_date=td,
            strategy="swing",
            ts_code="600001.SH",
            dim="trend",
            score=80,
            detail={},
        ),
        FilterScoreDaily(
            trade_date=td,
            strategy="swing",
            ts_code="600002.SH",
            dim="trend",
            score=70,
            detail={},
        ),
    ])
    sqlite_session.commit()

    picked = combine_scores(
        sqlite_session,
        td,
        dimension_weights={"trend": 1.0},
        hard_reject_cfg={},
        top_n=20,
        enable_ai=False,
        strategy="swing",
        regime_score=20,
        combine_cfg={
            "max_stocks_per_sector": 0,
            "market_regime_control": {
                "tiers": [{"min_score": 0, "top_n": 1, "position_scale": 0.2}],
                "min_final_score": 0,
            },
        },
    )

    rows = sqlite_session.query(SelectionResult).filter_by(trade_date=td, strategy="swing").all()
    assert picked == 1
    assert sum(1 for r in rows if r.picked) == 1
    assert {r.ts_code for r in rows if r.picked} == {"600001.SH"}
