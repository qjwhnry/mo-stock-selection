"""波段短线催化维度。"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.data_sources.calendar import previous_trading_day
from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline, StockBasic


class CatalystFilter(FilterBase):
    """断板反包 + 龙虎榜机构/游资催化，低权重使用。"""

    dim = "catalyst"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        catalyst: dict[str, dict[str, Any]] = {}

        # 断板反包作为短线催化，但只给低分，不能单独决定波段入选。
        for ts_code, rebound in _break_board_rebounds(session, trade_date).items():
            info = catalyst.setdefault(ts_code, {"score": 0.0, "detail": {}})
            info["score"] += 20
            info["detail"]["break_board_rebound"] = rebound

        seats_by_stock = repo.get_lhb_seats_today(session, trade_date)
        lhb_rows = repo.get_lhb_today(session, trade_date)
        for row in lhb_rows:
            if row.net_rate is None or row.net_rate <= 0:
                continue
            info = catalyst.setdefault(row.ts_code, {"score": 0.0, "detail": {}})
            seats = seats_by_stock.get(row.ts_code, [])
            inst_buy = sum((s.net_buy or 0.0) for s in seats if s.seat_type == "institution")
            hot_buy = sum((s.net_buy or 0.0) for s in seats if s.seat_type == "hot_money")
            if inst_buy >= 10_000_000:
                info["score"] += 25
                info["detail"]["institution_net_buy"] = round(inst_buy, 2)
            if hot_buy >= 5_000_000:
                info["score"] += 10
                info["detail"]["hot_money_net_buy"] = round(hot_buy, 2)

        results = [
            ScoreResult(ts_code, trade_date, self.dim, clamp(info["score"]), info["detail"])
            for ts_code, info in catalyst.items()
            if info["score"] > 0
        ]
        logger.info("CatalystFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _break_board_rebounds(session: Session, trade_date: date) -> dict[str, int]:
    """直接查表识别断板反包，避免 swing 催化维度隐式调用 short 的 LimitFilter。"""
    prev_trade_date = previous_trading_day(session, trade_date)
    if prev_trade_date is None:
        logger.warning("CatalystFilter: {} 找不到上一交易日，跳过断板反包", trade_date)
        return {}

    today_limit_codes = repo.get_limit_up_codes(session, trade_date)
    yesterday_limit_codes = repo.get_limit_up_codes(session, prev_trade_date)
    if not yesterday_limit_codes:
        return {}

    pct_rows = session.execute(
        select(DailyKline.ts_code, DailyKline.pct_chg)
        .where(DailyKline.trade_date == trade_date)
        .where(DailyKline.ts_code.in_(yesterday_limit_codes))
    ).all()
    active_codes = {
        row[0] for row in session.execute(select(StockBasic.ts_code)).all()
    }
    result: dict[str, int] = {}
    for ts_code, pct_chg in pct_rows:
        if ts_code not in active_codes or ts_code in today_limit_codes:
            continue
        bonus = _break_board_rebound_bonus(
            yesterday_was_limit_up=ts_code in yesterday_limit_codes,
            today_is_limit_up=False,
            today_pct_chg=pct_chg,
        )
        if bonus > 0:
            result[ts_code] = bonus
    return result


def _break_board_rebound_bonus(
    yesterday_was_limit_up: bool,
    today_is_limit_up: bool,
    today_pct_chg: float | None,
) -> int:
    """断板反包加分：昨涨停今没涨停但保持涨势。"""
    if not yesterday_was_limit_up or today_is_limit_up:
        return 0
    if today_pct_chg is None or today_pct_chg < 1.0:
        return 0
    if today_pct_chg >= 8.0:
        return 100
    if today_pct_chg >= 5.0:
        return 70
    if today_pct_chg >= 3.0:
        return 50
    return 30
