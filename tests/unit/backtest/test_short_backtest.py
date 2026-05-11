"""短线回测交易模拟测试。"""
from __future__ import annotations

from datetime import date

import pytest

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


def test_simulate_holding_period_applies_costs_to_net_return(sqlite_session) -> None:
    """成本扣费验证：买入 100，持有 2 日（entry 日 + 次日）次日收 105，扣完成本 net_realized 应约 +4.5%。

    注意：future_trade_dates(start=entry_date, days=N) 是 cal_date >= start + limit(N)，
    所以 holding_days=1 时 planned_exit == entry_date（当天退出），用不到次日 K 线。
    要让退出价为次日 close，holding_days 必须传 2。

    买入参考价 100，第 2 日收盘 105：
        raw_return = (105 - 100) / 100 × 100 = 5.0
        净收益按 _net_return：
            entry = 100 × (1 + 0.20%) × (1 + 0.025%) ≈ 100.225
            exit  = 105 × (1 - 0.20%) × (1 - 0.025%) × (1 - 0.05%) ≈ 104.711
            net   = (104.711 / 100.225 - 1) × 100 ≈ 4.476
    """
    sess = sqlite_session
    ts_code = "000001.SZ"
    # future_trade_dates 从 entry_date 开始（含），limit=2 → [4-2, 4-3]
    sess.add_all([
        TradeCal(cal_date=date(2026, 4, 1), is_open=True),
        TradeCal(cal_date=date(2026, 4, 2), is_open=True),
        TradeCal(cal_date=date(2026, 4, 3), is_open=True),
    ])
    # entry 日 K 线必须存在（_simulate_holding_period 会扫描 entry → exit 的全部日期）
    sess.add_all([
        DailyKline(ts_code=ts_code, trade_date=date(2026, 4, 2),
                   open=100.0, high=100.5, low=99.5, close=100.0,
                   pre_close=99.0, amount=100000.0, pct_chg=1.0),
        DailyKline(ts_code=ts_code, trade_date=date(2026, 4, 3),
                   open=100.0, high=106.0, low=100.0, close=105.0,
                   pre_close=100.0, amount=200000.0, pct_chg=5.0),
    ])
    sess.commit()

    cost_cfg = {
        "buy_slippage_pct": 0.20,
        "sell_slippage_pct": 0.20,
        "commission_pct": 0.025,
        "tax_pct": 0.05,
    }
    result = _simulate_holding_period(
        sess,
        ts_code=ts_code,
        entry_date=date(2026, 4, 2),
        entry_price=100.0,
        holding_days=2,
        stop_loss_pct=5.0,
        cost_cfg=cost_cfg,
    )
    # 返回的是 dict，不是 ORM 对象
    assert isinstance(result, dict)
    assert result["raw_return_pct"] == pytest.approx(5.0, abs=0.01)
    assert result["realized_return_pct"] == pytest.approx(5.0, abs=0.01)
    # 净收益（扣费后）应比 raw 低 ~0.5%
    assert result["net_raw_return_pct"] == pytest.approx(4.4763, abs=0.01)
    assert result["net_realized_return_pct"] == pytest.approx(4.4763, abs=0.01)
    # 没触发止损
    assert result["stop_hit"] is False
