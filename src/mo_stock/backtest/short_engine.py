"""短线策略回测引擎。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from config.settings import settings
from mo_stock.backtest.short_metrics import compute_short_metrics
from mo_stock.backtest.utils import (
    future_trade_dates,
    future_trade_dates_after,
    limit_up_gap_threshold,
    next_trade_date,
    trade_dates_between,
)
from mo_stock.filters.base import ScoreResult
from mo_stock.filters.short.lhb_filter import LhbFilter
from mo_stock.filters.short.limit_filter import LimitFilter
from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter
from mo_stock.filters.short.sector_filter import SectorFilter
from mo_stock.filters.short.theme_filter import ThemeFilter
from mo_stock.scorer.combine import (
    _build_hard_reject_map,
    _weighted_combine,
    apply_sector_cap,
)
from mo_stock.storage.models import DailyKline, LimitList, ShortBacktestTrade

DEFAULT_HOLDING_DAYS = [1, 2, 3, 5]


def run_short_backtest(
    session: Session,
    start: date,
    end: date,
    weights_cfg: dict[str, Any],
    top_n: int = 20,
    backtest_run_id: str | None = None,
    *,
    stop_loss_pct: float | None = None,
    holding_days: list[int] | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """运行 short 规则层回测并写入 short_backtest_trade。"""
    run_id = backtest_run_id or str(uuid4())
    session.execute(
        delete(ShortBacktestTrade)
        .where(ShortBacktestTrade.backtest_run_id == run_id)
    )

    bt_cfg = weights_cfg.get("short_backtest", {})
    effective_holding_days = holding_days or list(bt_cfg.get("holding_days", DEFAULT_HOLDING_DAYS))
    effective_stop = float(
        stop_loss_pct if stop_loss_pct is not None else bt_cfg.get("stop_loss_pct", 5.0)
    )
    cost_cfg = _cost_cfg(bt_cfg)
    skip_limit_gap = bool(bt_cfg.get("skip_limit_up_gap", True))

    trade_dates = trade_dates_between(session, start, end)
    rows: list[ShortBacktestTrade] = []
    for signal_date in trade_dates:
        entry_date = next_trade_date(session, signal_date)
        if entry_date is None:
            continue
        candidates = _rank_short_candidates(session, signal_date, weights_cfg, top_n)
        for candidate in candidates:
            entry_kline = session.get(DailyKline, (candidate["ts_code"], entry_date))
            if entry_kline is None or entry_kline.open is None:
                continue
            if skip_limit_gap and _is_limit_up_gap(
                session, candidate["ts_code"], signal_date, float(entry_kline.open),
            ):
                continue

            for days in effective_holding_days:
                outcome = _simulate_holding_period(
                    session,
                    ts_code=candidate["ts_code"],
                    entry_date=entry_date,
                    entry_price=float(entry_kline.open),
                    holding_days=int(days),
                    stop_loss_pct=effective_stop,
                    cost_cfg=cost_cfg,
                )
                detail = {
                    **outcome.pop("detail", {}),
                    "cost": cost_cfg,
                    "stop_loss_pct": effective_stop,
                }
                rows.append(ShortBacktestTrade(
                    backtest_run_id=run_id,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    ts_code=candidate["ts_code"],
                    holding_days=int(days),
                    rule_score=candidate["rule_score"],
                    active_dims=candidate["active_dims"],
                    dim_detail=candidate["dim_detail"],
                    rank_in_day=candidate["rank_in_day"],
                    sector_l1=candidate.get("sector_l1"),
                    entry_price=float(entry_kline.open),
                    detail=detail,
                    **outcome,
                ))

    if rows:
        session.add_all(rows)
    session.commit()

    metrics = compute_short_metrics(rows, effective_holding_days)
    report_path = _render_short_report(
        run_id=run_id,
        start=start,
        end=end,
        top_n=top_n,
        stop_loss_pct=effective_stop,
        holding_days=effective_holding_days,
        metrics=metrics,
        trades=rows,
        report_dir=report_dir or settings.report_dir,
    )
    logger.info(
        "short backtest {} {}→{} rows={} report={}",
        run_id, start, end, len(rows), report_path,
    )
    return {
        "backtest_run_id": run_id,
        "metrics": {
            "by_holding_days": {
                days: asdict(metric)
                for days, metric in metrics.by_holding_days.items()
            },
            "incomplete_exit_count": metrics.incomplete_exit_count,
            "stop_hit_count": metrics.stop_hit_count,
            "suspended_count": metrics.suspended_count,
            "limit_down_locked_count": metrics.limit_down_locked_count,
        },
        "report_path": str(report_path),
        "rows": len(rows),
    }


def _rank_short_candidates(
    session: Session,
    trade_date: date,
    cfg: dict[str, Any],
    top_n: int,
) -> list[dict[str, Any]]:
    filters = [
        LimitFilter(weights=cfg.get("limit_filter", {})),
        MoneyflowFilter(weights=cfg.get("moneyflow_filter", {})),
        LhbFilter(weights=cfg.get("lhb_filter", {})),
        SectorFilter(weights=cfg.get("sector_filter", {})),
        ThemeFilter(weights=cfg.get("theme_filter", {})),
    ]
    dim_results: dict[str, dict[str, ScoreResult]] = {}
    for filter_obj in filters:
        for result in filter_obj.score_all(session, trade_date):
            if result.score > 0:
                dim_results.setdefault(result.ts_code, {})[result.dim] = result

    weights = cfg.get("dimension_weights", {})
    hard_reject = cfg.get("hard_reject", {})
    reject_map = _build_hard_reject_map(session, trade_date, hard_reject, list(dim_results))

    scored: list[dict[str, Any]] = []
    for ts_code, scores in dim_results.items():
        dim_scores = {dim: result.score for dim, result in scores.items()}
        rule_score = round(_weighted_combine(dim_scores, weights), 2)
        dim_detail = {
            dim: {"score": round(result.score, 2), "detail": result.detail or {}}
            for dim, result in scores.items()
        }
        scored.append({
            "ts_code": ts_code,
            "rule_score": rule_score,
            "final_score": rule_score,
            "active_dims": len(scores),
            "dim_detail": dim_detail,
            "reject_reason": reject_map.get(ts_code),
        })

    scored.sort(
        key=lambda x: (-x["rule_score"], -x["active_dims"], x["ts_code"])
    )
    capped = apply_sector_cap(session, scored, top_n, cfg.get("combine", {}))
    return [
        {
            "ts_code": item["ts_code"],
            "rule_score": item["rule_score"],
            "active_dims": item["active_dims"],
            "dim_detail": item["dim_detail"],
            "rank_in_day": item["rank"],
            "sector_l1": item.get("sector_l1"),
        }
        for item in capped
        if item["picked"]
    ]


def _simulate_holding_period(
    session: Session,
    *,
    ts_code: str,
    entry_date: date,
    entry_price: float,
    holding_days: int,
    stop_loss_pct: float,
    cost_cfg: dict[str, float],
) -> dict[str, Any]:
    planned_dates = future_trade_dates(session, entry_date, holding_days)
    if len(planned_dates) < holding_days:
        return _incomplete_outcome()

    planned_exit = planned_dates[-1]
    missing_days = [
        d for d in planned_dates
        if session.get(DailyKline, (ts_code, d)) is None
    ]
    detail: dict[str, Any] = {}
    if missing_days:
        detail["suspended_in_holding"] = True
        detail["suspended_dates"] = [d.isoformat() for d in missing_days]

    actual_exit = planned_exit
    exit_kline = session.get(DailyKline, (ts_code, actual_exit))
    if exit_kline is None or exit_kline.close is None:
        next_exit = _first_available_kline_after(session, ts_code, planned_exit)
        if next_exit is None:
            outcome = _incomplete_outcome()
            outcome["detail"] = detail
            return outcome
        actual_exit, exit_kline = next_exit
        detail["actual_exit_note"] = (
            f"原定 {planned_exit.isoformat()} 停牌，顺延至 {actual_exit.isoformat()}"
        )

    assert exit_kline.close is not None
    raw_exit_price = float(exit_kline.close)
    raw_return = _pct(raw_exit_price, entry_price)
    dates_for_scan = trade_dates_between(session, entry_date, actual_exit)
    available_rows = [
        row for row in (session.get(DailyKline, (ts_code, d)) for d in dates_for_scan)
        if row is not None
    ]
    if detail.get("suspended_in_holding"):
        detail["actual_holding_days"] = len(dates_for_scan)
        detail["actual_stock_trading_days"] = len(available_rows)
    max_return = _max_return(available_rows, entry_price)
    max_drawdown = _max_drawdown(available_rows, entry_price)

    stop_price = entry_price * (1 - stop_loss_pct / 100)
    stop_hit = False
    stop_day: int | None = None
    exit_date = actual_exit
    exit_price = raw_exit_price
    exit_reason = "time_exit"
    for idx, current_date in enumerate(dates_for_scan, start=1):
        row = session.get(DailyKline, (ts_code, current_date))
        if row is None or row.low is None:
            continue
        if row.low <= stop_price:
            stop_hit = True
            stop_day = idx
            exit_date = current_date
            exit_price = stop_price
            exit_reason = "stop_loss"
            if _is_limit_down_locked(session, ts_code, row):
                detail["limit_down_locked"] = True
                detail["liquidity_note"] = "止损日接近跌停且收盘等于最低价，保守视为流动性受限"
                liquidity = _first_available_kline_after(session, ts_code, row.trade_date)
                if liquidity is not None and liquidity[1].close is not None:
                    detail["liquidity_exit_date"] = liquidity[0].isoformat()
                    detail["liquidity_exit_price"] = float(liquidity[1].close)
                    detail["liquidity_return_pct"] = round(
                        _pct(float(liquidity[1].close), entry_price), 4,
                    )
            break

    realized = _pct(exit_price, entry_price)
    return {
        "exit_date": exit_date,
        "exit_price": round(exit_price, 4),
        "raw_return_pct": round(raw_return, 4),
        "realized_return_pct": round(realized, 4),
        "net_raw_return_pct": round(_net_return(entry_price, raw_exit_price, cost_cfg), 4),
        "net_realized_return_pct": round(_net_return(entry_price, exit_price, cost_cfg), 4),
        "max_return_pct": round(max_return, 4) if max_return is not None else None,
        "max_drawdown_pct": round(max_drawdown, 4) if max_drawdown is not None else None,
        "stop_hit": stop_hit,
        "stop_day": stop_day,
        "exit_reason": exit_reason,
        "detail": detail,
    }


def _incomplete_outcome() -> dict[str, Any]:
    return {
        "exit_date": None,
        "exit_price": None,
        "raw_return_pct": None,
        "realized_return_pct": None,
        "net_raw_return_pct": None,
        "net_realized_return_pct": None,
        "max_return_pct": None,
        "max_drawdown_pct": None,
        "stop_hit": False,
        "stop_day": None,
        "exit_reason": "incomplete",
        "detail": {},
    }


def _is_limit_up_gap(
    session: Session,
    ts_code: str,
    signal_date: date,
    entry_open: float,
) -> bool:
    signal_kline = session.get(DailyKline, (ts_code, signal_date))
    if signal_kline is None or signal_kline.close is None or signal_kline.close <= 0:
        return False
    gap_pct = (entry_open / signal_kline.close - 1) * 100
    return gap_pct > limit_up_gap_threshold(ts_code)


def _is_limit_down_locked(session: Session, ts_code: str, row: DailyKline) -> bool:
    if row.trade_date is None:
        return False
    # 精确判断：查 limit_list 表是否有跌停记录
    ll = session.get(LimitList, (ts_code, row.trade_date))
    if ll is not None and ll.limit_type == "D":
        return True
    # Fallback 启发式：接近跌停且收盘等于最低价
    if row.pct_chg is None or row.close is None or row.low is None:
        return False
    near_limit_down = row.pct_chg <= -limit_up_gap_threshold(ts_code)
    close_at_low = abs(float(row.close) - float(row.low)) < 1e-6
    return near_limit_down and close_at_low


def _first_available_kline_after(
    session: Session,
    ts_code: str,
    current: date,
    *,
    max_days: int = 30,
) -> tuple[date, DailyKline] | None:
    for d in future_trade_dates_after(session, current, max_days):
        row = session.get(DailyKline, (ts_code, d))
        if row is not None and row.close is not None:
            return d, row
    return None


def _pct(exit_price: float, entry_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def _net_return(entry_price: float, exit_price: float, cfg: dict[str, float]) -> float:
    adjusted_entry = (
        entry_price
        * (1 + cfg.get("buy_slippage_pct", 0.0) / 100)
        * (1 + cfg.get("commission_pct", 0.0) / 100)
    )
    adjusted_exit = (
        exit_price
        * (1 - cfg.get("sell_slippage_pct", 0.0) / 100)
        * (1 - cfg.get("commission_pct", 0.0) / 100)
        * (1 - cfg.get("tax_pct", 0.0) / 100)
    )
    return _pct(adjusted_exit, adjusted_entry)


def _max_return(rows: list[DailyKline], entry_price: float) -> float | None:
    highs = [float(row.high) for row in rows if row.high is not None]
    if not highs:
        return None
    return _pct(max(highs), entry_price)


def _max_drawdown(rows: list[DailyKline], entry_price: float) -> float | None:
    lows = [float(row.low) for row in rows if row.low is not None]
    if not lows:
        return None
    return _pct(min(lows), entry_price)


def _cost_cfg(bt_cfg: dict[str, Any]) -> dict[str, float]:
    return {
        "buy_slippage_pct": float(bt_cfg.get("buy_slippage_pct", 0.0)),
        "sell_slippage_pct": float(bt_cfg.get("sell_slippage_pct", 0.0)),
        "commission_pct": float(bt_cfg.get("commission_pct", 0.0)),
        "tax_pct": float(bt_cfg.get("tax_pct", 0.0)),
    }


def _render_short_report(
    *,
    run_id: str,
    start: date,
    end: date,
    top_n: int,
    stop_loss_pct: float,
    holding_days: list[int],
    metrics: Any,
    trades: list[ShortBacktestTrade],
    report_dir: Path,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"short-backtest-{run_id[:8]}.md"
    lines = [
        "# 短线策略回测报告（规则层 run-once --skip-ai 口径）",
        "",
        f"- run_id: `{run_id}`",
        f"- 区间: {start.isoformat()} -> {end.isoformat()}",
        f"- TOP N: {top_n}",
        f"- 止损: {stop_loss_pct:.2f}%",
        "",
        "> 止损收益默认假设可成交；跌停锁仓保守口径见 detail.liquidity_*。",
        "> exit_based_cum_return 不是严格权益曲线。",
        "",
        "## 分持有期收益",
        "",
        "| holding | total | valid | win% | avg% | median% | payoff | max% | min% | stop% | exit_based_cum% |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for days in holding_days:
        m = metrics.by_holding_days[days]
        lines.append(
            f"| {days}d | {m.total_trades} | {m.valid_trades} | {m.win_rate:.2f} | "
            f"{m.avg_return:.2f} | {m.median_return:.2f} | {m.payoff_ratio:.2f} | "
            f"{m.max_return:.2f} | {m.max_loss:.2f} | {m.stop_hit_rate:.2f} | "
            f"{m.exit_based_cum_return:.2f} |"
        )
    lines.extend([
        "",
        "## 覆盖与风险",
        "",
        f"- incomplete_exit_count: {metrics.incomplete_exit_count}",
        f"- stop_hit_count: {metrics.stop_hit_count}",
        f"- suspended_count: {metrics.suspended_count}",
        f"- limit_down_locked_count: {metrics.limit_down_locked_count}",
        "",
        "## 每日 TOP 清单",
        "",
        "| signal_date | rank | ts_code | holding | rule | realized% | exit_reason | sector |",
        "|---|---:|---|---:|---:|---:|---|---|",
    ])
    for row in sorted(
        trades,
        key=lambda r: (r.signal_date, r.rank_in_day or 0, r.ts_code, r.holding_days),
    ):
        realized = "" if row.realized_return_pct is None else f"{row.realized_return_pct:.2f}"
        lines.append(
            f"| {row.signal_date} | {row.rank_in_day or ''} | {row.ts_code} | "
            f"{row.holding_days} | {float(row.rule_score or 0):.2f} | {realized} | "
            f"{row.exit_reason or ''} | {row.sector_l1 or ''} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
