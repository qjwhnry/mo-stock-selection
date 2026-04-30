"""波段行业持续性维度。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.swing.swing_utils import recent_trade_dates_asc
from mo_stock.storage import repo
from mo_stock.storage.models import Moneyflow, SwDaily


class SectorSwingFilter(FilterBase):
    """申万一级行业多日强度 + 行业资金派生聚合。"""

    dim = "sector_swing"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        dates10 = recent_trade_dates_asc(session, trade_date, 10)
        if not dates10:
            return []
        l1_map = repo.get_index_member_l1_map(session)
        if not l1_map:
            logger.warning("SectorSwingFilter: index_member 为空")
            return []

        sw_rows = session.execute(
            select(SwDaily).where(SwDaily.trade_date.in_(dates10))
        ).scalars().all()
        by_sector: dict[str, list[SwDaily]] = defaultdict(list)
        for row in sw_rows:
            by_sector[row.sw_code].append(row)

        sector_scores = _score_sectors(by_sector, dates10)
        moneyflow_by_sector = _sector_moneyflow_map(session, dates10[-5:], l1_map)
        for l1_code, total in moneyflow_by_sector.items():
            if total > 0:
                sector_scores.setdefault(l1_code, {"score": 0.0, "detail": {}})
                sector_scores[l1_code]["score"] += 25
                sector_scores[l1_code]["detail"]["sector_moneyflow_5d_wan"] = round(total, 2)

        results: list[ScoreResult] = []
        for ts_code, l1_code in l1_map.items():
            info = sector_scores.get(l1_code)
            if not info:
                continue
            score = clamp(info["score"])
            if score > 0:
                detail = dict(info["detail"])
                detail["l1_code"] = l1_code
                results.append(ScoreResult(ts_code, trade_date, self.dim, score, detail))

        logger.info("SectorSwingFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _score_sectors(
    by_sector: dict[str, list[SwDaily]],
    dates10: list[date],
) -> dict[str, dict[str, Any]]:
    dates5 = set(dates10[-5:])
    ret5: dict[str, float] = {}
    ret10: dict[str, float] = {}
    stable: set[str] = set()
    for code, rows in by_sector.items():
        rows = sorted(rows, key=lambda r: r.trade_date)
        vals5 = [r.pct_change or 0.0 for r in rows if r.trade_date in dates5]
        vals10 = [r.pct_change or 0.0 for r in rows]
        if vals5:
            ret5[code] = sum(vals5)
            if all(v > -3 for v in vals5):
                stable.add(code)
        if vals10:
            ret10[code] = sum(vals10)

    top5 = {code: rank for rank, code in enumerate(_rank_keys(ret5)[:5], start=1)}
    top10 = {code: rank for rank, code in enumerate(_rank_keys(ret10)[:8], start=1)}
    result: dict[str, dict[str, Any]] = {}
    for code in set(top5) | set(top10) | stable:
        score = 0.0
        detail: dict[str, Any] = {}
        if code in top5:
            score += 30
            detail["sector_5d_rank"] = top5[code]
            detail["sector_5d_pct_sum"] = round(ret5.get(code, 0.0), 2)
        if code in top10:
            score += 25
            detail["sector_10d_rank"] = top10[code]
            detail["sector_10d_pct_sum"] = round(ret10.get(code, 0.0), 2)
        if code in stable:
            score += 20
            detail["sector_pullback_stable"] = True
        result[code] = {"score": score, "detail": detail}
    return result


def _rank_keys(values: dict[str, float]) -> list[str]:
    return sorted(values, key=lambda code: values[code], reverse=True)


def _sector_moneyflow_map(
    session: Session,
    dates5: list[date],
    l1_map: dict[str, str],
) -> dict[str, float]:
    if not dates5:
        return {}
    stock_to_l1 = l1_map
    rows = session.execute(
        select(Moneyflow.ts_code, func.sum(Moneyflow.net_mf_amount))
        .where(Moneyflow.trade_date.in_(dates5))
        .where(Moneyflow.ts_code.in_(stock_to_l1.keys()))
        .group_by(Moneyflow.ts_code)
    ).all()
    result: dict[str, float] = defaultdict(float)
    for ts_code, net_mf in rows:
        l1_code = stock_to_l1.get(ts_code)
        if l1_code:
            result[l1_code] += net_mf or 0.0
    return dict(result)
