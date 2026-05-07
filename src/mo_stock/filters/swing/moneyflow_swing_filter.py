"""波段资金持续性维度。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.moneyflow_utils import confirmed_positive_net, has_price_confirmation
from mo_stock.filters.swing.swing_utils import recent_trade_dates_asc
from mo_stock.storage.models import DailyKline, Moneyflow


class MoneyflowSwingFilter(FilterBase):
    """5/10 日资金持续性。"""

    dim = "moneyflow_swing"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        dates10 = recent_trade_dates_asc(session, trade_date, 10)
        if not dates10:
            return []
        dates5 = set(dates10[-5:])
        rows = session.execute(
            select(Moneyflow).where(Moneyflow.trade_date.in_(dates10))
        ).scalars().all()
        price_map: dict[tuple[str, date], tuple[float | None, float | None]] = {
            (ts_code, row_date): (open_price, close_price)
            for ts_code, row_date, open_price, close_price in session.execute(
                select(
                    DailyKline.ts_code,
                    DailyKline.trade_date,
                    DailyKline.open,
                    DailyKline.close,
                )
                .where(DailyKline.trade_date.in_(dates10))
            ).all()
        }

        by_stock: dict[str, list[Moneyflow]] = defaultdict(list)
        for row in rows:
            by_stock[row.ts_code].append(row)

        results: list[ScoreResult] = []
        for ts_code, items in by_stock.items():
            score = 0.0
            detail: dict[str, Any] = {}
            sum10 = sum(
                confirmed_positive_net(
                    r.net_mf_amount,
                    *price_map.get((r.ts_code, r.trade_date), (None, None)),
                )
                for r in items
            )
            sum5 = sum(
                confirmed_positive_net(
                    r.net_mf_amount,
                    *price_map.get((r.ts_code, r.trade_date), (None, None)),
                )
                for r in items
                if r.trade_date in dates5
            )
            positive_days5 = sum(
                1
                for r in items
                if r.trade_date in dates5
                and (r.net_mf_amount or 0.0) > 0
                and has_price_confirmation(
                    *price_map.get((r.ts_code, r.trade_date), (None, None)),
                )
            )
            unconfirmed_inflow_days5 = sum(
                1
                for r in items
                if r.trade_date in dates5
                and (r.net_mf_amount or 0.0) > 0
                and not has_price_confirmation(
                    *price_map.get((r.ts_code, r.trade_date), (None, None)),
                )
            )
            detail["net_mf_5d_wan"] = round(sum5, 2)
            detail["net_mf_10d_wan"] = round(sum10, 2)
            detail["positive_days_5d"] = positive_days5
            if unconfirmed_inflow_days5:
                detail["unconfirmed_inflow_days_5d"] = unconfirmed_inflow_days5

            if sum5 > 0:
                score += 25
                detail["net_mf_5d_positive"] = True
            if sum10 > 0:
                score += 25
                detail["net_mf_10d_positive"] = True
            if positive_days5 >= 3:
                score += 20
                detail["positive_days_bonus"] = 20

            big_positive_days = 0
            small_up_big_down = False
            for r in items:
                open_price, close_price = price_map.get(
                    (r.ts_code, r.trade_date), (None, None),
                )
                buy_big = (r.buy_lg_amount or 0.0) + (r.buy_elg_amount or 0.0)
                sell_big = (r.sell_lg_amount or 0.0) + (r.sell_elg_amount or 0.0)
                big_net = buy_big - sell_big
                if big_net > 0 and has_price_confirmation(open_price, close_price):
                    big_positive_days += 1
                small_net = (r.buy_sm_amount or 0.0) - (r.sell_sm_amount or 0.0)
                if r.trade_date in dates5 and small_net > 0 and big_net < 0:
                    small_up_big_down = True
            if len(items) >= 5 and big_positive_days / len(items) >= 0.5:
                score += 20
                detail["big_order_positive_days"] = big_positive_days
            if small_up_big_down:
                score -= 30
                detail["small_up_big_down_penalty"] = -30
            if unconfirmed_inflow_days5 >= 2:
                score -= 20
                detail["unconfirmed_inflow_penalty"] = -20

            final = clamp(score)
            if final > 0:
                results.append(ScoreResult(ts_code, trade_date, self.dim, final, detail))

        logger.info("MoneyflowSwingFilter: {} 加分股 {} 只", trade_date, len(results))
        return results
