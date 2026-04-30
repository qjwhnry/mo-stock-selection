"""波段市场环境评分。"""
from __future__ import annotations

from datetime import date

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.swing.swing_utils import pct_change, recent_trade_dates_asc, sma
from mo_stock.storage.models import DailyKline

DEFAULT_REGIME_SCORE = 50.0


class MarketRegimeFilter:
    """全市场统一 regime_score，只用于组合层 top_n / 仓位控制。"""

    def score_market(self, session: Session, trade_date: date) -> float:
        """返回 0-100 市场环境分；缺数时降级中性 50。"""
        try:
            score = self._score_market(session, trade_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarketRegimeFilter: {} 评分失败，降级为 50：{}", trade_date, exc)
            return DEFAULT_REGIME_SCORE
        if score is None:
            logger.warning("MarketRegimeFilter: {} 指数/广度数据不足，降级为 50", trade_date)
            return DEFAULT_REGIME_SCORE
        return max(0.0, min(100.0, score))

    def _score_market(self, session: Session, trade_date: date) -> float | None:
        dates = recent_trade_dates_asc(session, trade_date, 70)
        if len(dates) < 20:
            return None
        index_rows = session.execute(
            select(DailyKline)
            .where(DailyKline.ts_code == "000300.SH")
            .where(DailyKline.trade_date.in_(dates))
            .order_by(DailyKline.trade_date)
        ).scalars().all()
        if len(index_rows) < 20:
            return None

        closes = [r.close for r in index_rows]
        close = closes[-1]
        if close is None:
            return None

        score = 0.0
        ma20 = sma(closes, 20)
        ma60 = sma(closes, 60)
        if ma20 and close > ma20:
            score += 20
        if _ma20_slope_positive(closes):
            score += 20
        breadth = _market_breadth(session, trade_date)
        if breadth is None:
            return None
        if breadth > 55:
            score += 20
        pct10 = pct_change(closes[-10], close) if len(closes) >= 10 else None
        if pct10 is not None and pct10 > 0:
            score += 20
        if pct10 is not None and pct10 < -5:
            score -= 40
        if breadth < 35:
            score -= 30
        if _ma20_crossed_below_ma60(closes, ma20, ma60):
            score -= 30
        return score


def _market_breadth(session: Session, trade_date: date) -> float | None:
    rows = session.execute(
        select(DailyKline.pct_chg)
        .where(DailyKline.trade_date == trade_date)
        .where(~DailyKline.ts_code.in_(["000300.SH", "000001.SH"]))
        .where(DailyKline.pct_chg.isnot(None))
    ).all()
    if not rows:
        return None
    up = sum(1 for (pct,) in rows if pct is not None and pct > 0)
    return up / len(rows) * 100


def _ma20_slope_positive(closes: list[float | None]) -> bool:
    if len(closes) < 25:
        return False
    prev = sma(closes[:-5], 20)
    cur = sma(closes, 20)
    return prev is not None and cur is not None and cur > prev


def _ma20_crossed_below_ma60(
    closes: list[float | None], ma20: float | None, ma60: float | None,
) -> bool:
    if len(closes) < 61 or ma20 is None or ma60 is None:
        return False
    prev_ma20 = sma(closes[:-1], 20)
    prev_ma60 = sma(closes[:-1], 60)
    return (
        prev_ma20 is not None and prev_ma60 is not None
        and prev_ma20 >= prev_ma60 and ma20 < ma60
    )
