"""波段回踩承接维度。"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.swing.swing_utils import (
    distance_pct,
    group_klines_by_stock,
    mean,
    recent_trade_dates_asc,
    sma,
)
from mo_stock.storage import repo


class PullbackFilter(FilterBase):
    """趋势中的缩量回踩和重新转强。"""

    dim = "pullback"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        stock_codes = {s.ts_code for s in repo.list_active_stocks(session)}
        trade_dates = recent_trade_dates_asc(session, trade_date, 30)
        grouped = group_klines_by_stock(session, trade_dates, stock_codes)
        results: list[ScoreResult] = []

        for ts_code, rows in grouped.items():
            if len(rows) < 20:
                continue
            closes = [r.close for r in rows]
            vols = [r.vol for r in rows]
            close = closes[-1]
            if close is None:
                continue
            score = 0.0
            detail: dict[str, Any] = {}

            drawdown = _recent_drawdown_pct(closes, 5)
            if drawdown is not None:
                detail["drawdown_5d_pct"] = round(drawdown, 2)
                if 3 <= drawdown <= 12:
                    score += 25
                    detail["healthy_pullback"] = True

            ma10 = sma(closes, 10)
            ma20 = sma(closes, 20)
            dist10 = abs(distance_pct(close, ma10) or 999.0)
            dist20 = abs(distance_pct(close, ma20) or 999.0)
            if min(dist10, dist20) <= 5:
                score += 25
                detail["near_ma10_or_ma20"] = True
                detail["distance_ma10_pct"] = round(dist10, 2) if dist10 < 999 else None
                detail["distance_ma20_pct"] = round(dist20, 2) if dist20 < 999 else None

            if _pullback_volume_shrunk(vols):
                score += 20
                detail["pullback_volume_shrunk"] = True

            ma5 = sma(closes, 5)
            prev_close = closes[-2] if len(closes) >= 2 else None
            if ma5 and ma10 and prev_close and (prev_close < ma5 <= close or prev_close < ma10 <= close):
                score += 20
                detail["recovered_ma5_or_ma10"] = True

            if _long_upper_shadow_with_volume(rows):
                score -= 30
                detail["long_upper_shadow_penalty"] = -30

            final = clamp(score)
            if final > 0:
                results.append(ScoreResult(ts_code, trade_date, self.dim, final, detail))

        logger.info("PullbackFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _recent_drawdown_pct(closes: list[float | None], window: int) -> float | None:
    """计算窗口内最大回撤（时序峰值→谷值，单次遍历 running peak）。

    例：[10, 8, 11] → running peak 在 10，8 时回撤 20%，之后 peak 更新到 11。
    不会因为全局最高点在末尾而漏掉前面的回撤。
    """
    recent = closes[-window:]
    if len(recent) < 2:
        return None
    running_peak: float | None = None
    max_drawdown = 0.0
    for c in recent:
        if c is None:
            continue
        if running_peak is None or c > running_peak:
            running_peak = c
        elif running_peak > 0:
            dd = (running_peak - c) / running_peak * 100
            if dd > max_drawdown:
                max_drawdown = dd
    return max_drawdown if max_drawdown > 0 else None


def _pullback_volume_shrunk(vols: list[float | None]) -> bool:
    if len(vols) < 10:
        return False
    rise_avg = mean([v for v in vols[-10:-5] if v is not None])
    pullback_avg = mean([v for v in vols[-5:] if v is not None])
    return rise_avg is not None and pullback_avg is not None and pullback_avg < rise_avg


def _long_upper_shadow_with_volume(rows) -> bool:
    if len(rows) < 20:
        return False
    today = rows[-1]
    if not all(v is not None for v in (today.open, today.high, today.close, today.vol)):
        return False
    avg20 = sma([r.vol for r in rows], 20)
    if avg20 is None or avg20 <= 0 or today.vol <= 2 * avg20:
        return False
    body_top = max(today.open, today.close)
    if today.close <= 0:
        return False
    upper_shadow_pct = (today.high - body_top) / today.close * 100
    return upper_shadow_pct >= 3
