"""短线回测指标计算。"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from statistics import median, stdev
from typing import Any


@dataclass(frozen=True)
class ShortHoldingMetrics:
    total_trades: int
    valid_trades: int
    win_rate: float
    avg_return: float
    median_return: float
    avg_win: float
    avg_loss: float
    payoff_ratio: float
    max_return: float
    max_loss: float
    sharpe_like: float
    stop_hit_rate: float
    exit_based_cum_return: float


@dataclass(frozen=True)
class ShortBacktestMetrics:
    by_holding_days: dict[int, ShortHoldingMetrics]
    incomplete_exit_count: int
    stop_hit_count: int
    suspended_count: int
    limit_down_locked_count: int


def compute_short_metrics(
    trades: Iterable[Any],
    holding_days: list[int],
    *,
    return_attr: str = "realized_return_pct",
) -> ShortBacktestMetrics:
    """按持有期计算短线回测指标。

    `trades` 可以是 ORM 对象，也可以是测试中的 SimpleNamespace，只要具备
    holding_days / realized_return_pct / exit_date / stop_hit / detail 字段。
    """
    trade_list = list(trades)
    by_holding: dict[int, ShortHoldingMetrics] = {}
    for days in holding_days:
        rows = [t for t in trade_list if getattr(t, "holding_days", None) == days]
        by_holding[days] = _compute_one_holding(rows, return_attr=return_attr)

    return ShortBacktestMetrics(
        by_holding_days=by_holding,
        incomplete_exit_count=sum(
            1 for t in trade_list if getattr(t, "exit_reason", None) == "incomplete"
        ),
        stop_hit_count=sum(1 for t in trade_list if bool(getattr(t, "stop_hit", False))),
        suspended_count=sum(
            1 for t in trade_list
            if bool((getattr(t, "detail", None) or {}).get("suspended_in_holding"))
        ),
        limit_down_locked_count=sum(
            1 for t in trade_list
            if bool((getattr(t, "detail", None) or {}).get("limit_down_locked"))
        ),
    )


def _compute_one_holding(rows: list[Any], *, return_attr: str) -> ShortHoldingMetrics:
    total = len(rows)
    valid_pairs: list[tuple[date, float]] = []
    returns: list[float] = []
    for row in rows:
        value = getattr(row, return_attr, None)
        if value is None:
            continue
        pct = float(value)
        returns.append(pct)
        exit_date = getattr(row, "exit_date", None)
        if exit_date is not None:
            valid_pairs.append((exit_date, pct))

    if not returns:
        return ShortHoldingMetrics(
            total_trades=total,
            valid_trades=0,
            win_rate=0.0,
            avg_return=0.0,
            median_return=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            payoff_ratio=0.0,
            max_return=0.0,
            max_loss=0.0,
            sharpe_like=0.0,
            stop_hit_rate=_stop_hit_rate(rows, total),
            exit_based_cum_return=0.0,
        )

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    avg_return = sum(returns) / len(returns)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = avg_win / abs(avg_loss) if avg_loss < 0 else 0.0
    volatility = stdev(returns) if len(returns) >= 2 else 0.0
    sharpe = avg_return / volatility if volatility > 0 else 0.0

    return ShortHoldingMetrics(
        total_trades=total,
        valid_trades=len(returns),
        win_rate=len(wins) / len(returns) * 100,
        avg_return=avg_return,
        median_return=median(returns),
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff,
        max_return=max(returns),
        max_loss=min(returns),
        sharpe_like=sharpe,
        stop_hit_rate=_stop_hit_rate(rows, total),
        exit_based_cum_return=_exit_based_cum_return(valid_pairs),
    )


def _stop_hit_rate(rows: list[Any], total: int) -> float:
    if total <= 0:
        return 0.0
    return sum(1 for row in rows if bool(getattr(row, "stop_hit", False))) / total * 100


def _exit_based_cum_return(valid_pairs: list[tuple[date, float]]) -> float:
    by_exit: dict[date, list[float]] = defaultdict(list)
    for exit_date, pct in valid_pairs:
        by_exit[exit_date].append(pct)

    compounded = 1.0
    for exit_date in sorted(by_exit):
        values = by_exit[exit_date]
        daily_avg = sum(values) / len(values)
        compounded *= 1 + daily_avg / 100
    return (compounded - 1) * 100

