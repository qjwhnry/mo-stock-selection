"""swing 回测指标测试。"""
from __future__ import annotations

from datetime import date

from mo_stock.backtest.engine import _future_trade_dates, _next_trade_date, _trade_dates_between
from mo_stock.backtest.metrics import compute_trade_metrics
from mo_stock.storage.models import DailyKline, TradeCal


def test_compute_trade_metrics() -> None:
    metrics = compute_trade_metrics([10.0, -5.0, 4.0, -2.0])

    assert metrics.total_trades == 4
    assert metrics.win_rate == 50.0
    assert metrics.avg_win_pct == 7.0
    assert metrics.avg_loss_pct == -3.5
    assert metrics.payoff_ratio == 2.0
    assert metrics.avg_pnl_pct == 1.75
    assert metrics.max_loss_pct == -5.0


def test_compute_trade_metrics_empty() -> None:
    metrics = compute_trade_metrics([])

    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0


def test_backtest_trade_dates_use_trade_cal_not_daily_kline(sqlite_session) -> None:
    d1 = date(2026, 4, 28)
    d2 = date(2026, 4, 29)
    d3 = date(2026, 4, 30)
    sqlite_session.add_all([
        TradeCal(cal_date=d1, is_open=True, pretrade_date=None),
        TradeCal(cal_date=d2, is_open=False, pretrade_date=d1),
        TradeCal(cal_date=d3, is_open=True, pretrade_date=d1),
        DailyKline(
            ts_code="600001.SH",
            trade_date=d2,
            open=10,
            high=11,
            low=9,
            close=10,
            pre_close=10,
            pct_chg=0,
            vol=1000,
            amount=100000,
        ),
    ])
    sqlite_session.commit()

    assert _trade_dates_between(sqlite_session, d1, d3) == [d1, d3]
    assert _next_trade_date(sqlite_session, d1) == d3
    assert _future_trade_dates(sqlite_session, d1, 3) == [d1, d3]
