"""波段策略 filter 共享工具。"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline


def recent_trade_dates_asc(session: Session, end_date: date, days: int) -> list[date]:
    """最近 N 个交易日，按时间升序返回。"""
    return sorted(repo.get_recent_trade_dates(session, end_date, days))


def group_klines_by_stock(
    session: Session,
    trade_dates: Iterable[date],
    stock_codes: set[str] | None = None,
) -> dict[str, list[DailyKline]]:
    """批量读取 K 线并按股票分组，组内按日期升序。"""
    dates = list(trade_dates)
    if not dates:
        return {}
    stmt = select(DailyKline).where(DailyKline.trade_date.in_(dates))
    if stock_codes is not None:
        if not stock_codes:
            return {}
        stmt = stmt.where(DailyKline.ts_code.in_(stock_codes))
    stmt = stmt.order_by(DailyKline.ts_code, DailyKline.trade_date)

    grouped: dict[str, list[DailyKline]] = defaultdict(list)
    for row in session.execute(stmt).scalars().all():
        grouped[row.ts_code].append(row)
    return dict(grouped)


def mean(values: list[float]) -> float | None:
    """非空均值。"""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def sma(values: list[float | None], window: int) -> float | None:
    """简单移动平均。"""
    if len(values) < window:
        return None
    clean = [v for v in values[-window:] if v is not None]
    if len(clean) < window:
        return None
    return sum(clean) / window


def pct_change(start: float | None, end: float | None) -> float | None:
    """涨跌幅百分比。"""
    if start is None or end is None or start <= 0:
        return None
    return (end - start) / start * 100


def distance_pct(price: float | None, ref: float | None) -> float | None:
    """price 相对 ref 的偏离百分比。"""
    if price is None or ref is None or ref <= 0:
        return None
    return (price - ref) / ref * 100
