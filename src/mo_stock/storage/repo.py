"""仓储层：统一的读取方法，供 filters / ai / report 调用。

**重要约定**：
- ingest 层是唯一写入方；repo 主要暴露**读方法**，写方法只给 ingest 用（以 `upsert_` 前缀区分）。
- 所有方法接受 Session 作为第一个参数，便于在同一事务内批量调用。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mo_stock.storage.models import (
    AnnsRaw,
    Base,
    DailyBasic,
    DailyKline,
    Lhb,
    LimitList,
    Moneyflow,
    NewsRaw,
    StockBasic,
    SwDaily,
    TradeCal,
)

# ========================================================================
# 读方法
# ========================================================================

def get_stock_basic(session: Session, ts_code: str) -> StockBasic | None:
    """查询单只股票的基础信息。"""
    return session.get(StockBasic, ts_code)


def list_active_stocks(session: Session, exclude_st: bool = True) -> Sequence[StockBasic]:
    """列出全部 A 股，可选排除 ST。"""
    stmt = select(StockBasic)
    if exclude_st:
        stmt = stmt.where(StockBasic.is_st.is_(False))
    return session.execute(stmt).scalars().all()


def is_trade_date(session: Session, d: date) -> bool:
    """判断某日是否交易日。"""
    row = session.get(TradeCal, d)
    return bool(row and row.is_open)


def get_recent_trade_dates(session: Session, end: date, n: int) -> list[date]:
    """获取截至 end 的最近 n 个交易日（降序，包含 end 本身如果 end 是交易日）。"""
    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date <= end)
        .order_by(TradeCal.cal_date.desc())
        .limit(n)
    )
    return list(session.execute(stmt).scalars().all())


def get_limit_list(
    session: Session,
    trade_date: date,
    limit_type: str = "U",
) -> Sequence[LimitList]:
    """获取某日涨停/跌停/炸板列表。"""
    stmt = (
        select(LimitList)
        .where(LimitList.trade_date == trade_date)
        .where(LimitList.limit_type == limit_type)
    )
    return session.execute(stmt).scalars().all()


def get_moneyflow(session: Session, trade_date: date) -> Sequence[Moneyflow]:
    """获取某日全市场资金流向。"""
    stmt = select(Moneyflow).where(Moneyflow.trade_date == trade_date)
    return session.execute(stmt).scalars().all()


def get_moneyflow_series(
    session: Session,
    ts_code: str,
    end_date: date,
    days: int = 3,
) -> Sequence[Moneyflow]:
    """获取单只股票近 N 日资金流，降序。"""
    stmt = (
        select(Moneyflow)
        .where(Moneyflow.ts_code == ts_code)
        .where(Moneyflow.trade_date <= end_date)
        .order_by(Moneyflow.trade_date.desc())
        .limit(days)
    )
    return session.execute(stmt).scalars().all()


def get_kline_range(
    session: Session,
    ts_code: str,
    start: date,
    end: date,
) -> Sequence[DailyKline]:
    """获取单只股票某段时间的日线。"""
    stmt = (
        select(DailyKline)
        .where(DailyKline.ts_code == ts_code)
        .where(DailyKline.trade_date.between(start, end))
        .order_by(DailyKline.trade_date)
    )
    return session.execute(stmt).scalars().all()


def get_recent_lhb(
    session: Session,
    ts_code: str,
    end_date: date,
    days: int = 3,
) -> Sequence[Lhb]:
    """获取单只股票近 N 日龙虎榜记录。"""
    start = end_date - timedelta(days=days)
    stmt = (
        select(Lhb)
        .where(Lhb.ts_code == ts_code)
        .where(Lhb.trade_date.between(start, end_date))
        .order_by(Lhb.trade_date.desc())
    )
    return session.execute(stmt).scalars().all()


def get_news_for_stock(
    session: Session,
    ts_code: str,
    end_date: date,
    days: int = 7,
) -> Sequence[NewsRaw]:
    """获取单只股票近 N 日新闻。"""
    start = datetime.combine(end_date - timedelta(days=days), datetime.min.time())
    end = datetime.combine(end_date, datetime.max.time())
    stmt = (
        select(NewsRaw)
        .where(NewsRaw.ts_code == ts_code)
        .where(NewsRaw.pub_time.between(start, end))
        .order_by(NewsRaw.pub_time.desc())
    )
    return session.execute(stmt).scalars().all()


def get_anns_for_stock(
    session: Session,
    ts_code: str,
    end_date: date,
    days: int = 7,
) -> Sequence[AnnsRaw]:
    """获取单只股票近 N 日公告。"""
    start = end_date - timedelta(days=days)
    stmt = (
        select(AnnsRaw)
        .where(AnnsRaw.ts_code == ts_code)
        .where(AnnsRaw.ann_date.between(start, end_date))
        .order_by(AnnsRaw.ann_date.desc())
    )
    return session.execute(stmt).scalars().all()


# ========================================================================
# 写方法（仅 ingest 使用）
# ========================================================================

def upsert_rows(
    session: Session,
    model: type[Base],
    rows: Iterable[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str] | None = None,
) -> int:
    """PG 原生 upsert：ON CONFLICT DO UPDATE。

    Args:
        session: 当前事务 Session
        model: ORM 模型类，例如 DailyKline
        rows: 要插入的字典列表
        conflict_cols: 主键或唯一键列（触发 ON CONFLICT 的键）
        update_cols: 冲突时要更新的列；None 表示全部更新（除冲突键）

    Returns:
        upsert 的行数
    """
    rows_list = list(rows)
    if not rows_list:
        return 0

    # pg_insert 接受 ORM 类；运行时会自动取 __table__
    stmt = pg_insert(model).values(rows_list)

    # 计算冲突时要更新的列（排除冲突键本身）
    if update_cols is None:
        update_cols = [c.name for c in model.__table__.columns if c.name not in conflict_cols]

    set_map = {col: stmt.excluded[col] for col in update_cols}
    stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_map)

    # DML 执行返回 CursorResult，有 rowcount；基类 Result 无，mypy 不识别
    result = session.execute(stmt)
    return result.rowcount or 0  # type: ignore[attr-defined]


# ========================================================================
# 具体表的 upsert 便捷封装
# ========================================================================

def upsert_daily_kline(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, DailyKline, rows, conflict_cols=["ts_code", "trade_date"])


def upsert_daily_basic(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, DailyBasic, rows, conflict_cols=["ts_code", "trade_date"])


def upsert_limit_list(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, LimitList, rows, conflict_cols=["ts_code", "trade_date"])


def upsert_moneyflow(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, Moneyflow, rows, conflict_cols=["ts_code", "trade_date"])


def upsert_stock_basic(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, StockBasic, rows, conflict_cols=["ts_code"])


def upsert_trade_cal(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, TradeCal, rows, conflict_cols=["cal_date"])


def upsert_sw_daily(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, SwDaily, rows, conflict_cols=["sw_code", "trade_date"])
