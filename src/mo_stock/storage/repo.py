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
    IndexMember,
    Lhb,
    LimitList,
    Moneyflow,
    NewsRaw,
    StockBasic,
    SwDaily,
    ThsIndex,
    ThsMember,
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


def get_limit_up_codes(session: Session, trade_date: date) -> set[str]:
    """获取某日涨停（U 类型）股票代码集合。LimitFilter 用来识别"昨日涨停"和"今日涨停"。"""
    stmt = (
        select(LimitList.ts_code)
        .where(LimitList.trade_date == trade_date)
        .where(LimitList.limit_type == "U")
    )
    return set(session.execute(stmt).scalars().all())


def get_l1_limit_count_map(session: Session, trade_date: date) -> dict[str, int]:
    """获取当日每个一级板块涨停股数量 {l1_code: count}。

    用于 SectorLimitHeat 加分：同一板块涨停股越多 → 板块热度越高 → 给同板块
    非涨停股加分（PLAN.md「当日涨停股只作为板块信号」的核心实现）。
    """
    # 子查询：拿当日涨停股的 l1_code（join index_member）
    stmt = (
        select(IndexMember.l1_code, IndexMember.ts_code)
        .where(IndexMember.l1_code.isnot(None))
        .where(
            IndexMember.ts_code.in_(
                select(LimitList.ts_code)
                .where(LimitList.trade_date == trade_date)
                .where(LimitList.limit_type == "U")
            )
        )
    )
    result: dict[str, int] = {}
    for l1, _ts in session.execute(stmt).all():
        if l1 is not None:
            result[l1] = result.get(l1, 0) + 1
    return result


def get_lhb_today(session: Session, trade_date: date) -> Sequence[Lhb]:
    """获取当日全市场龙虎榜上榜股（LhbFilter.score_all 主源）。"""
    stmt = select(Lhb).where(Lhb.trade_date == trade_date)
    return session.execute(stmt).scalars().all()


def get_sw_daily_for_codes(
    session: Session, trade_date: date, sw_codes: set[str],
) -> list[tuple[str, float | None]]:
    """获取指定 sw_code 集合在某交易日的涨跌幅 [(sw_code, pct_change), ...]。

    SectorFilter 用此方法+ index_member.l1_code 集合精确取出一级板块涨幅。
    （sw_daily 表里 sw_code LIKE '801%' 包含一/二/三级共 180 个，必须按
    index_member 实际的 31 个 l1_code 白名单 filter，否则二三级板块会污染 TOP 排名。）
    """
    if not sw_codes:
        return []
    stmt = (
        select(SwDaily.sw_code, SwDaily.pct_change)
        .where(SwDaily.trade_date == trade_date)
        .where(SwDaily.sw_code.in_(sw_codes))
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def get_sw_daily_3d_avg_for_codes(
    session: Session, trade_date: date, sw_codes: set[str],
) -> dict[str, float]:
    """近 3 日均涨幅 {sw_code: avg_pct_change}，限定在 sw_codes 集合内。

    用于 SectorFilter 的"3 日趋势加成"。如果板块某天数据缺失，按已有的天平均。
    """
    if not sw_codes:
        return {}
    start = trade_date - timedelta(days=3)
    stmt = (
        select(SwDaily.sw_code, SwDaily.pct_change)
        .where(SwDaily.trade_date.between(start, trade_date))
        .where(SwDaily.sw_code.in_(sw_codes))
        .where(SwDaily.pct_change.isnot(None))
    )
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for sw_code, pct in session.execute(stmt).all():
        sums[sw_code] = sums.get(sw_code, 0.0) + (pct or 0.0)
        counts[sw_code] = counts.get(sw_code, 0) + 1
    return {sc: sums[sc] / counts[sc] for sc in sums if counts[sc] > 0}


def get_index_member_l1_map(session: Session) -> dict[str, str]:
    """股票 → 申万一级板块代码映射 {ts_code: l1_code}。

    SectorFilter 用此 map 把每只股关联到所属一级板块，再 join sw_daily 拿涨幅。
    全表扫，5700 行，结果缓存在调用方进程内即可（板块归属慢变量）。
    """
    stmt = select(IndexMember.ts_code, IndexMember.l1_code).where(
        IndexMember.l1_code.isnot(None),
    )
    # SQL 已过滤 NOT NULL，运行时 l1_code 不会是 None；显式 if 也帮 mypy 收紧类型
    return {ts: l1 for ts, l1 in session.execute(stmt).all() if l1 is not None}


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

    P0-9：开发期断言 conflict_cols 与表主键对齐。当主键被改而调用方忘记更新
    conflict_cols 时会立刻报错（而不是等运行时撞 ON CONFLICT 失败）。
    若调用方使用唯一索引（非主键）触发冲突，可显式传 update_cols 跳过此校验
    场景，但本项目现有所有 upsert 都用主键，因此这条断言始终生效。
    """
    rows_list = list(rows)
    if not rows_list:
        return 0

    # primary_key 在 SQLAlchemy 类型 stub 中标为 Iterable[NamedColumn]，
    # 运行时是 PrimaryKeyConstraint 暴露 .columns；用 list() 强制取列对象后再取 name
    pk_names = {col.name for col in list(model.__table__.primary_key)}
    # 仅当 conflict_cols 完全等于主键列时直接放行；不一致就 raise，避免
    # 运行时 PG 抛 "no unique or exclusion constraint matching the ON CONFLICT
    # specification"（错误信息晦涩，不易定位）。
    if set(conflict_cols) != pk_names:
        raise ValueError(
            f"upsert_rows: conflict_cols={conflict_cols} 与 {model.__name__} "
            f"主键 {sorted(pk_names)} 不一致；请检查模型定义或 repo 封装"
        )

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


def upsert_lhb(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, Lhb, rows, conflict_cols=["trade_date", "ts_code"])


def upsert_index_member(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, IndexMember, rows, conflict_cols=["ts_code"])


def upsert_ths_index(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, ThsIndex, rows, conflict_cols=["ts_code"])


def upsert_ths_member(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, ThsMember, rows, conflict_cols=["ts_code", "con_code"])


def upsert_stock_basic(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, StockBasic, rows, conflict_cols=["ts_code"])


def upsert_trade_cal(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, TradeCal, rows, conflict_cols=["cal_date"])


def upsert_sw_daily(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    return upsert_rows(session, SwDaily, rows, conflict_cols=["sw_code", "trade_date"])
