"""波段风险与流动性维度。"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.swing.swing_utils import (
    distance_pct,
    group_klines_by_stock,
    pct_change,
    recent_trade_dates_asc,
    sma,
)
from mo_stock.storage import repo
from mo_stock.storage.models import DailyBasic


class RiskLiquidityFilter(FilterBase):
    """流动性、波动率、短期透支度质量分。"""

    dim = "risk_liquidity"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        stock_codes = {s.ts_code for s in repo.list_active_stocks(session)}
        dates = recent_trade_dates_asc(session, trade_date, 20)
        grouped = group_klines_by_stock(session, dates, stock_codes)
        basics = {
            b.ts_code: b for b in session.execute(
                select(DailyBasic).where(DailyBasic.trade_date == trade_date)
            ).scalars().all()
        }
        results: list[ScoreResult] = []

        for ts_code, rows in grouped.items():
            if len(rows) < 20:
                continue
            closes = [r.close for r in rows]
            amounts = [r.amount for r in rows]
            close = closes[-1]
            if close is None:
                continue
            score = 0.0
            detail: dict[str, Any] = {}

            # daily.amount 单位千元，转亿元：/1e5
            avg_amount_yi = sum((a or 0.0) for a in amounts) / len(amounts) / 1e5
            detail["avg_amount_20d_yi"] = round(avg_amount_yi, 2)
            if avg_amount_yi < 0.5:
                score -= 30
                detail["low_liquidity_penalty"] = -30
            elif avg_amount_yi >= 5:
                score += 35
                detail["liquidity_bonus"] = 35
            elif avg_amount_yi >= 2:
                score += 28
                detail["liquidity_bonus"] = 28
            else:
                score += 18
                detail["liquidity_bonus"] = 18

            highs = [r.high for r in rows if r.high is not None]
            lows = [r.low for r in rows if r.low is not None]
            if highs and lows:
                amplitude = (max(highs) - min(lows)) / close * 100
                detail["amplitude_20d_pct"] = round(amplitude, 2)
                if 10 <= amplitude <= 30:
                    score += 25
                    detail["healthy_volatility_bonus"] = 25
                elif 30 < amplitude <= 40:
                    score += 12
                    detail["healthy_volatility_bonus"] = 12
                elif amplitude > 40:
                    score -= 25
                    detail["high_volatility_penalty"] = -25

            pct3 = pct_change(closes[-3], close) if len(closes) >= 3 else None
            avg20_vol = sma([r.vol for r in rows], 20)
            if pct3 is not None:
                detail["pct_3d"] = round(pct3, 2)
            if pct3 is not None and pct3 > 15 and rows[-1].vol and avg20_vol and rows[-1].vol > 1.5 * avg20_vol:
                score -= 30
                detail["short_term_overheat_penalty"] = -30

            ma20 = sma(closes, 20)
            dist = distance_pct(close, ma20)
            if dist is not None:
                detail["distance_ma20_pct"] = round(dist, 2)
                if -8 <= dist <= 12:
                    score += 25
                    detail["ma20_distance_bonus"] = 25
                elif -12 <= dist <= 18:
                    score += 15
                    detail["ma20_distance_bonus"] = 15
                elif dist > 20:
                    score -= 25
                    detail["far_above_ma20_penalty"] = -25

            basic = basics.get(ts_code)
            if basic and basic.turnover_rate is not None:
                detail["turnover_rate"] = basic.turnover_rate
                if 1 <= basic.turnover_rate <= 8:
                    score += 15
                    detail["turnover_bonus"] = 15
                elif 0.5 <= basic.turnover_rate <= 12:
                    score += 8
                    detail["turnover_bonus"] = 8

            final = clamp(score)
            if final > 0:
                results.append(ScoreResult(ts_code, trade_date, self.dim, final, detail))

        logger.info("RiskLiquidityFilter: {} 加分股 {} 只", trade_date, len(results))
        return results
