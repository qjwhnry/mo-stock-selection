"""波段趋势结构 + 量价确认维度。"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.swing_utils import (
    distance_pct,
    group_klines_by_stock,
    pct_change,
    recent_trade_dates_asc,
    sma,
)
from mo_stock.storage import repo


class TrendFilter(FilterBase):
    """MA 结构、20 日涨幅与量价确认。"""

    dim = "trend"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        stock_codes = {s.ts_code for s in repo.list_active_stocks(session)}
        trade_dates = recent_trade_dates_asc(session, trade_date, 70)
        grouped = group_klines_by_stock(session, trade_dates, stock_codes)
        results: list[ScoreResult] = []

        for ts_code, rows in grouped.items():
            if len(rows) < 20:
                continue
            closes = [r.close for r in rows]
            close = closes[-1]
            if close is None:
                continue

            ma5 = sma(closes, 5)
            ma10 = sma(closes, 10)
            ma20 = sma(closes, 20)
            ma60 = sma(closes, 60)
            score = 0.0
            detail: dict[str, Any] = {
                "close": round(close, 2),
                "ma5": round(ma5, 2) if ma5 else None,
                "ma10": round(ma10, 2) if ma10 else None,
                "ma20": round(ma20, 2) if ma20 else None,
                "ma60": round(ma60, 2) if ma60 else None,
            }

            if ma20 and close > ma20:
                score += 15
                detail["above_ma20"] = True
            if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
                score += 20
                detail["ma_bullish"] = True
            if _ma20_slope_positive(closes):
                score += 15
                detail["ma20_slope_positive"] = True
            if (ma60 and close > ma60) or _ma20_crossed_above_ma60(closes):
                score += 10
                detail["above_ma60_or_cross"] = True

            pct20 = pct_change(closes[-20], close) if len(closes) >= 20 else None
            if pct20 is not None:
                detail["pct_20d"] = round(pct20, 2)
                if 5 <= pct20 <= 35:
                    score += 15
                    detail["pct_20d_bonus"] = 15
                if pct20 > 60:
                    score -= 30
                    detail["overheated_penalty"] = -30

            volume_score, volume_detail = _volume_price_score(rows)
            score += volume_score
            detail.update(volume_detail)

            final = clamp(score)
            if final > 0:
                results.append(ScoreResult(ts_code, trade_date, self.dim, final, detail))

        logger.info("TrendFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _ma20_slope_positive(closes: list[float | None]) -> bool:
    """近 5 日 MA20 抬升。"""
    if len(closes) < 25:
        return False
    prev = sma(closes[:-5], 20)
    cur = sma(closes, 20)
    return prev is not None and cur is not None and cur > prev


def _ma20_crossed_above_ma60(closes: list[float | None]) -> bool:
    """MA20 上穿 MA60 的近似判定。"""
    if len(closes) < 61:
        return False
    prev_ma20 = sma(closes[:-1], 20)
    prev_ma60 = sma(closes[:-1], 60)
    cur_ma20 = sma(closes, 20)
    cur_ma60 = sma(closes, 60)
    return (
        prev_ma20 is not None and prev_ma60 is not None
        and cur_ma20 is not None and cur_ma60 is not None
        and prev_ma20 <= prev_ma60 and cur_ma20 > cur_ma60
    )


def _volume_price_score(rows) -> tuple[float, dict[str, Any]]:
    """量价确认子项。"""
    detail: dict[str, Any] = {}
    if len(rows) < 20:
        return 0.0, detail
    vols = [r.vol for r in rows]
    closes = [r.close for r in rows]
    avg20 = sma(vols, 20)
    if avg20 is None or avg20 <= 0:
        return 0.0, detail

    score = 0.0
    avg5 = sma(vols, 5)
    prev_avg5 = sma(vols[:-5], 5) if len(vols) >= 10 else None
    if avg5 and prev_avg5 and avg5 > prev_avg5:
        score += 10
        detail["volume_trend_up"] = True

    today_vol = vols[-1]
    pct5 = pct_change(closes[-5], closes[-1]) if len(closes) >= 5 else None
    if today_vol and today_vol > 1.5 * avg20 and pct5 and pct5 > 3:
        score += 10
        detail["breakout_volume_bonus"] = 10

    ma20 = sma(closes, 20)
    dist = distance_pct(closes[-1], ma20)
    if today_vol and dist is not None and abs(dist) <= 5 and today_vol < 0.7 * avg20:
        score += 5
        detail["pullback_volume_shrink_bonus"] = 5

    if today_vol and today_vol > 1.5 * avg20 and pct5 is not None and pct5 <= 1:
        score -= 20
        detail["volume_stall_penalty"] = -20

    return score, detail
