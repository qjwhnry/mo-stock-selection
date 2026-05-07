"""回测共享工具函数。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.storage.models import TradeCal


def limit_up_gap_threshold(ts_code: str) -> float:
    """根据板块涨跌幅限制返回高开跳过阈值（%）。"""
    if ts_code.endswith(".BJ"):
        return 29.5
    prefix = ts_code[:3]
    if prefix in {"300", "301", "688", "689"}:
        return 19.5
    return 9.5


def trade_dates_between(session: Session, start: date, end: date) -> list[date]:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date >= start)
        .where(TradeCal.cal_date <= end)
        .order_by(TradeCal.cal_date)
    )
    return list(session.execute(stmt).scalars().all())


def next_trade_date(session: Session, current: date) -> date | None:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date > current)
        .order_by(TradeCal.cal_date)
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def future_trade_dates(session: Session, start: date, days: int) -> list[date]:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date >= start)
        .order_by(TradeCal.cal_date)
        .limit(days)
    )
    return list(session.execute(stmt).scalars().all())


def future_trade_dates_after(session: Session, current: date, days: int) -> list[date]:
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date > current)
        .order_by(TradeCal.cal_date)
        .limit(days)
    )
    return list(session.execute(stmt).scalars().all())

