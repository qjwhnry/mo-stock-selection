"""回测指标计算。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    payoff_ratio: float
    avg_pnl_pct: float
    max_loss_pct: float


def compute_trade_metrics(pnls: list[float]) -> BacktestMetrics:
    """按单笔收益率计算基础回测指标。"""
    if not pnls:
        return BacktestMetrics(
            total_trades=0,
            win_rate=0.0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            payoff_ratio=0.0,
            avg_pnl_pct=0.0,
            max_loss_pct=0.0,
        )
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = avg_win / abs(avg_loss) if avg_loss < 0 else 0.0
    return BacktestMetrics(
        total_trades=len(pnls),
        win_rate=len(wins) / len(pnls) * 100,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        payoff_ratio=payoff,
        avg_pnl_pct=sum(pnls) / len(pnls),
        max_loss_pct=min(pnls),
    )
