"""短线回测交易模拟测试。"""
from __future__ import annotations

from datetime import date

from mo_stock.backtest.short_engine import _is_limit_down_locked, _simulate_holding_period
from mo_stock.storage.models import DailyKline, TradeCal


def _add_open_day(session, d: date) -> None:
    session.add(TradeCal(cal_date=d, is_open=True))


def _add_kline(
    session,
    ts_code: str,
    d: date,
    *,
    open_: float = 10.0,
    high: float = 10.5,
    low: float = 9.8,
    close: float = 10.0,
    pct_chg: float = 0.0,
) -> None:
    session.add(DailyKline(
        ts_code=ts_code,
        trade_date=d,
        open=open_,
        high=high,
        low=low,
        close=close,
        pre_close=10.0,
        pct_chg=pct_chg,
        vol=1000,
        amount=100000,
    ))


def test_simulate_holding_period_stops_and_keeps_raw_return(sqlite_session) -> None:
    code = "600001.SH"
    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 5)
    d3 = date(2026, 1, 6)
    for d in [d1, d2, d3]:
        _add_open_day(sqlite_session, d)
    _add_kline(sqlite_session, code, d1, low=9.7, close=10.2)
    _add_kline(sqlite_session, code, d2, low=9.4, close=9.6)
    _add_kline(sqlite_session, code, d3, low=9.8, close=11.0)
    sqlite_session.commit()

    result = _simulate_holding_period(
        sqlite_session,
        ts_code=code,
        entry_date=d1,
        entry_price=10.0,
        holding_days=3,
        stop_loss_pct=5.0,
        cost_cfg={},
    )

    assert result["stop_hit"] is True
    assert result["stop_day"] == 2
    assert result["exit_date"] == d2
    assert result["exit_price"] == 9.5
    assert result["realized_return_pct"] == -5.0
    assert result["raw_return_pct"] == 10.0
    assert result["exit_reason"] == "stop_loss"


def test_simulate_holding_period_writes_incomplete_when_future_data_missing(sqlite_session) -> None:
    code = "600002.SH"
    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 5)
    for d in [d1, d2]:
        _add_open_day(sqlite_session, d)
    _add_kline(sqlite_session, code, d1, close=10.1)
    sqlite_session.commit()

    result = _simulate_holding_period(
        sqlite_session,
        ts_code=code,
        entry_date=d1,
        entry_price=10.0,
        holding_days=3,
        stop_loss_pct=5.0,
        cost_cfg={},
    )

    assert result["exit_reason"] == "incomplete"
    assert result["realized_return_pct"] is None


def test_limit_down_locked_requires_close_at_low(sqlite_session) -> None:
    code = "600003.SH"
    d = date(2026, 1, 2)
    row_not_locked = DailyKline(
        ts_code=code,
        trade_date=d,
        open=10,
        high=10,
        low=9,
        close=9.2,
        pre_close=10,
        pct_chg=-9.8,
        vol=1000,
        amount=100000,
    )
    row_locked = DailyKline(
        ts_code=code,
        trade_date=d,
        open=10,
        high=10,
        low=9,
        close=9,
        pre_close=10,
        pct_chg=-9.8,
        vol=1000,
        amount=100000,
    )

    assert _is_limit_down_locked(sqlite_session, code, row_not_locked) is False
    assert _is_limit_down_locked(sqlite_session, code, row_locked) is True


def test_suspension_then_stop_loss_counts_calendar_days(sqlite_session) -> None:
    """中途停牌后再触发止损，stop_day 应按交易日历计数（停牌日占位）。"""
    code = "600004.SH"
    d1 = date(2026, 1, 2)   # entry (day 1)
    d2 = date(2026, 1, 5)   # day 2 — 停牌，无 K 线
    d3 = date(2026, 1, 6)   # day 3 — 复牌，low 触发止损
    for d in [d1, d2, d3]:
        _add_open_day(sqlite_session, d)
    _add_kline(sqlite_session, code, d1, low=9.8, close=10.2)
    # d2 无 K 线（停牌）
    _add_kline(sqlite_session, code, d3, low=9.4, close=9.6)
    sqlite_session.commit()

    result = _simulate_holding_period(
        sqlite_session,
        ts_code=code,
        entry_date=d1,
        entry_price=10.0,
        holding_days=3,
        stop_loss_pct=5.0,
        cost_cfg={},
    )

    # stop_day 按交易日历计数：d1=1, d2=2(停牌占位), d3=3
    assert result["stop_hit"] is True
    assert result["stop_day"] == 3
    assert result["exit_date"] == d3
    assert result["detail"]["suspended_in_holding"] is True
