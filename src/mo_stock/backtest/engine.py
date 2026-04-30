"""Swing 策略回测引擎。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mo_stock.backtest.metrics import compute_trade_metrics
from mo_stock.filters.base import ScoreResult
from mo_stock.filters.catalyst_filter import CatalystFilter
from mo_stock.filters.market_regime_filter import MarketRegimeFilter
from mo_stock.filters.moneyflow_swing_filter import MoneyflowSwingFilter
from mo_stock.filters.pullback_filter import PullbackFilter
from mo_stock.filters.risk_liquidity_filter import RiskLiquidityFilter
from mo_stock.filters.sector_swing_filter import SectorSwingFilter
from mo_stock.filters.theme_swing_filter import ThemeSwingFilter
from mo_stock.filters.trend_filter import TrendFilter
from mo_stock.scorer.combine import _build_hard_reject_map, _weighted_combine
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline, SwingPosition, TradeCal


def run_swing_backtest(
    session: Session,
    start: date,
    end: date,
    weights_cfg: dict[str, Any],
    top_n: int = 20,
    backtest_run_id: str | None = None,
) -> dict[str, Any]:
    """运行 swing 回测并写入 swing_position(mode='backtest')。"""
    run_id = backtest_run_id or str(uuid4())
    session.execute(
        delete(SwingPosition)
        .where(SwingPosition.mode == "backtest")
        .where(SwingPosition.backtest_run_id == run_id)
    )

    trade_dates = _trade_dates_between(session, start, end)
    if not trade_dates:
        return {"backtest_run_id": run_id, "metrics": asdict(compute_trade_metrics([]))}

    pnls: list[float] = []
    for trade_date in trade_dates:
        next_date = _next_trade_date(session, trade_date)
        if next_date is None:
            continue
        candidates = _rank_swing_candidates(session, trade_date, weights_cfg, top_n)
        for item in candidates:
            entry_kline = session.get(DailyKline, (item["ts_code"], next_date))
            if entry_kline is None or entry_kline.open is None:
                continue
            outcome = _simulate_trade(
                session,
                item["ts_code"],
                next_date,
                entry_kline.open,
                weights_cfg,
            )
            if outcome is None:
                continue
            pnls.append(outcome["pnl_pct"])
            session.add(SwingPosition(
                mode="backtest",
                backtest_run_id=run_id,
                trade_date=outcome["exit_date"],
                ts_code=item["ts_code"],
                status=outcome["status"],
                entry_price=entry_kline.open,
                entry_date=next_date,
                stop_loss_price=outcome["stop_loss_price"],
                target_price=outcome["target_price"],
                atr_at_entry=outcome["atr_pct"],
                max_price=outcome["max_price"],
                pnl_pct=outcome["pnl_pct"],
                exit_reason=outcome["exit_reason"],
                holding_days=outcome["holding_days"],
                detail={
                    "signal_date": trade_date.isoformat(),
                    "rule_score": item["rule_score"],
                    "regime_score": item["regime_score"],
                },
            ))
    session.commit()
    metrics = compute_trade_metrics(pnls)
    logger.info(
        "swing backtest {} {}→{} trades={} win_rate={:.2f}% payoff={:.2f}",
        run_id, start, end, metrics.total_trades, metrics.win_rate, metrics.payoff_ratio,
    )
    return {"backtest_run_id": run_id, "metrics": asdict(metrics)}


def _rank_swing_candidates(
    session: Session,
    trade_date: date,
    cfg: dict[str, Any],
    top_n: int,
) -> list[dict[str, Any]]:
    filters = [
        TrendFilter(weights=cfg.get("trend_filter", {})),
        PullbackFilter(weights=cfg.get("pullback_filter", {})),
        MoneyflowSwingFilter(weights=cfg.get("moneyflow_swing_filter", {})),
        SectorSwingFilter(weights=cfg.get("sector_swing_filter", {})),
        ThemeSwingFilter(weights=cfg.get("theme_swing_filter", {})),
        CatalystFilter(weights=cfg.get("catalyst_filter", {})),
        RiskLiquidityFilter(weights=cfg.get("risk_liquidity_filter", {})),
    ]
    dim_scores: dict[str, dict[str, ScoreResult]] = {}
    for filter_obj in filters:
        for result in filter_obj.score_all(session, trade_date):
            dim_scores.setdefault(result.ts_code, {})[result.dim] = result

    weights = cfg.get("dimension_weights", {})
    hard_reject = cfg.get("hard_reject", {})
    reject_map = _build_hard_reject_map(session, trade_date, hard_reject, list(dim_scores))
    regime_score = MarketRegimeFilter().score_market(session, trade_date)
    market_cfg = cfg.get("market_regime_control", {})
    effective_top_n = _effective_top_n(regime_score, market_cfg, top_n)
    min_final_score = float(market_cfg.get("min_final_score", 0))

    ranked: list[dict[str, Any]] = []
    for ts_code, scores in dim_scores.items():
        if ts_code in reject_map:
            continue
        rule_score = _weighted_combine({d: r.score for d, r in scores.items()}, weights)
        if rule_score < min_final_score:
            continue
        ranked.append({
            "ts_code": ts_code,
            "rule_score": round(rule_score, 2),
            "regime_score": regime_score,
            "active_dim_count": len(scores),
        })
    ranked.sort(key=lambda x: (-x["rule_score"], -x["active_dim_count"], x["ts_code"]))
    return ranked[:effective_top_n]


def _simulate_trade(
    session: Session,
    ts_code: str,
    entry_date: date,
    entry_price: float,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    dates = _future_trade_dates(session, entry_date, 20)
    if not dates:
        return None
    atr_pct = _atr_pct(session, ts_code, entry_date, cfg.get("stop_loss", {}))
    stop_pct = _stop_loss_pct(atr_pct, cfg.get("stop_loss", {}))
    stop_loss_price = entry_price * (1 - stop_pct / 100)
    target_price = entry_price * 1.10
    max_price = entry_price

    for idx, d in enumerate(dates, start=1):
        row = session.get(DailyKline, (ts_code, d))
        if row is None:
            continue
        if row.high is not None:
            max_price = max(max_price, row.high)
        if row.low is not None and row.low <= stop_loss_price:
            return _trade_outcome(
                d, idx, entry_price, stop_loss_price, stop_loss_price,
                target_price, atr_pct, max_price, "stop_loss",
            )
        if row.high is not None and row.high >= target_price:
            return _trade_outcome(
                d, idx, entry_price, target_price, stop_loss_price,
                target_price, atr_pct, max_price, "target",
            )
    last_date = dates[-1]
    last = session.get(DailyKline, (ts_code, last_date))
    if last is None or last.close is None:
        return None
    return _trade_outcome(
        last_date, len(dates), entry_price, last.close, stop_loss_price,
        target_price, atr_pct, max_price, "time_exit",
    )


def _trade_outcome(
    exit_date: date,
    holding_days: int,
    entry_price: float,
    exit_price: float,
    stop_loss_price: float,
    target_price: float,
    atr_pct: float,
    max_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    return {
        "status": "stopped" if exit_reason == "stop_loss" else "exited",
        "exit_date": exit_date,
        "holding_days": holding_days,
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "atr_pct": atr_pct,
        "max_price": max_price,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
    }


def _atr_pct(session: Session, ts_code: str, entry_date: date, cfg: dict[str, Any]) -> float:
    period = int(cfg.get("atr_period", 20))
    dates = repo.get_recent_trade_dates(session, entry_date, period + 1)
    rows = session.execute(
        select(DailyKline)
        .where(DailyKline.ts_code == ts_code)
        .where(DailyKline.trade_date.in_(dates))
        .order_by(DailyKline.trade_date)
    ).scalars().all()
    if len(rows) < 2:
        return 4.0
    trs: list[float] = []
    for prev, cur in zip(rows, rows[1:], strict=False):
        if None in (cur.high, cur.low, prev.close):
            continue
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    if not trs or rows[-1].close is None or rows[-1].close <= 0:
        return 4.0
    return sum(trs[-period:]) / min(len(trs), period) / rows[-1].close * 100


def _stop_loss_pct(atr_pct: float, cfg: dict[str, Any]) -> float:
    multiplier = float(cfg.get("atr_multiplier", 1.5))
    min_pct = float(cfg.get("min_stop_pct", 0.04)) * 100
    max_pct = float(cfg.get("max_stop_pct", 0.10)) * 100
    return max(min_pct, min(multiplier * atr_pct, max_pct))


def _trade_dates_between(session: Session, start: date, end: date) -> list[date]:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date >= start)
        .where(TradeCal.cal_date <= end)
        .order_by(TradeCal.cal_date)
    )
    return list(session.execute(stmt).scalars().all())


def _next_trade_date(session: Session, current: date) -> date | None:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date > current)
        .order_by(TradeCal.cal_date)
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _future_trade_dates(session: Session, start: date, days: int) -> list[date]:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date >= start)
        .order_by(TradeCal.cal_date)
        .limit(days)
    )
    return list(session.execute(stmt).scalars().all())


def _effective_top_n(regime_score: float, cfg: dict[str, Any], fallback: int) -> int:
    for tier in sorted(cfg.get("tiers", []), key=lambda t: float(t.get("min_score", 0)), reverse=True):
        if regime_score >= float(tier.get("min_score", 0)):
            return min(fallback, int(tier.get("top_n", fallback)))
    return fallback
