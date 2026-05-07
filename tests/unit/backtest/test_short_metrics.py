"""短线回测指标计算测试。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from mo_stock.backtest.short_metrics import compute_short_metrics


def _trade(
    *,
    holding_days: int,
    realized: float | None,
    exit_date: date | None,
    stop_hit: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        holding_days=holding_days,
        realized_return_pct=realized,
        net_realized_return_pct=realized,
        exit_date=exit_date,
        stop_hit=stop_hit,
    )


def test_compute_short_metrics_by_holding_days() -> None:
    trades = [
        _trade(holding_days=1, realized=2.0, exit_date=date(2026, 1, 2)),
        _trade(holding_days=1, realized=-1.0, exit_date=date(2026, 1, 2), stop_hit=True),
        _trade(holding_days=1, realized=None, exit_date=None),
        _trade(holding_days=2, realized=4.0, exit_date=date(2026, 1, 3)),
    ]

    result = compute_short_metrics(trades, holding_days=[1, 2])

    one_day = result.by_holding_days[1]
    assert one_day.total_trades == 3
    assert one_day.valid_trades == 2
    assert one_day.win_rate == 50.0
    assert one_day.avg_return == 0.5
    assert round(one_day.stop_hit_rate, 4) == round(100 / 3, 4)
    assert round(one_day.exit_based_cum_return, 4) == 0.5

    two_day = result.by_holding_days[2]
    assert two_day.total_trades == 1
    assert two_day.valid_trades == 1
    assert two_day.win_rate == 100.0
    assert round(two_day.exit_based_cum_return, 4) == 4.0
