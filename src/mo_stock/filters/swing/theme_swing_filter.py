"""波段题材持续性维度。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.swing.swing_utils import recent_trade_dates_asc
from mo_stock.storage import repo
from mo_stock.storage.models import ThsConceptMoneyflow, ThsDaily


class ThemeSwingFilter(FilterBase):
    """同花顺题材多日排名 + 资金确认。"""

    dim = "theme_swing"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        dates5 = recent_trade_dates_asc(session, trade_date, 5)
        if not dates5:
            return []

        theme_scores = _theme_score_map(session, dates5)
        if not theme_scores:
            logger.warning("ThemeSwingFilter: {} 无题材信号", trade_date)
            return []

        stock_concepts = repo.get_stock_to_concepts_map(session)
        results: list[ScoreResult] = []
        for ts_code, concepts in stock_concepts.items():
            best_code: str | None = None
            best: dict[str, Any] | None = None
            for concept in concepts:
                info = theme_scores.get(concept)
                if info and (best is None or info["score"] > best["score"]):
                    best = info
                    best_code = concept
            if not best or best["score"] <= 0:
                continue
            detail = dict(best["detail"])
            detail["best_concept"] = best_code
            results.append(
                ScoreResult(ts_code, trade_date, self.dim, clamp(best["score"]), detail)
            )

        logger.info("ThemeSwingFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _theme_score_map(session: Session, dates5: list[date]) -> dict[str, dict[str, Any]]:
    rows = session.execute(
        select(ThsDaily).where(ThsDaily.trade_date.in_(dates5))
    ).scalars().all()
    by_date: dict[date, list[ThsDaily]] = defaultdict(list)
    by_theme_pct: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_date[row.trade_date].append(row)
        if row.pct_change is not None:
            by_theme_pct[row.ts_code].append(row.pct_change)

    rank_points: dict[str, int] = defaultdict(int)
    rank_history: dict[str, list[int]] = defaultdict(list)
    for _date, day_rows in sorted(by_date.items()):
        ranked = sorted(
            [r for r in day_rows if r.pct_change is not None],
            key=lambda r: r.pct_change or 0,
            reverse=True,
        )[:10]
        for rank, row in enumerate(ranked, start=1):
            rank_points[row.ts_code] += max(0, 11 - rank)
            rank_history[row.ts_code].append(rank)

    mf_rows = session.execute(
        select(ThsConceptMoneyflow.ts_code, ThsConceptMoneyflow.net_amount)
        .where(ThsConceptMoneyflow.trade_date.in_(dates5))
    ).all()
    mf_sum: dict[str, float] = defaultdict(float)
    for ts_code, net_amount in mf_rows:
        mf_sum[ts_code] += net_amount or 0.0

    result: dict[str, dict[str, Any]] = {}
    top_themes = sorted(rank_points, key=lambda code: rank_points[code], reverse=True)[:10]
    for rank, code in enumerate(top_themes, start=1):
        score = 30.0
        detail: dict[str, Any] = {
            "theme_5d_rank": rank,
            "theme_rank_points": rank_points[code],
        }
        if mf_sum.get(code, 0.0) > 0:
            score += 25
            detail["theme_moneyflow_positive"] = True
            detail["theme_net_amount_yi"] = round(mf_sum[code], 2)
        ranks = rank_history.get(code, [])
        if len(ranks) >= 2 and ranks[-1] < ranks[0]:
            score += 20
            detail["theme_rank_improving"] = True
        avg_pct = sum(by_theme_pct.get(code, [0.0])) / max(len(by_theme_pct.get(code, [])), 1)
        detail["theme_avg_pct_5d"] = round(avg_pct, 2)
        result[code] = {"score": score, "detail": detail}
    return result
