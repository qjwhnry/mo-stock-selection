"""交易日历 / 股票状态工具。

所有涉及"这是哪个交易日？""这只股票能不能选？"的判断都走这里，
避免逻辑散落在各处。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mo_stock.storage.models import StockBasic, TradeCal


def is_trading_day(session: Session, d: date) -> bool:
    """指定日期是否为 A 股交易日。"""
    row = session.get(TradeCal, d)
    return bool(row and row.is_open)


def previous_trading_day(session: Session, d: date) -> date | None:
    """返回 d 之前最近的交易日（不含 d 本身）。

    P2-14：兜底窗口改为 14 天。春节连续放假 ~9 自然日 + 周末 + 临时调休
    最长可达 11~12 天，原 10 天兜底是临界值，14 天给出安全余量。
    """
    row = session.get(TradeCal, d)
    if row and row.pretrade_date:
        return row.pretrade_date

    # 兜底：向前最多找 14 天（春节最长场景下足够）
    cursor = d - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(session, cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def recent_trading_days(session: Session, end: date, n: int) -> list[date]:
    """返回截至 end 的最近 n 个交易日（升序）。

    如 end 本身是交易日则包含，否则从 end 之前的最近交易日开始往前。
    """
    from sqlalchemy import select

    stmt = (
        select(TradeCal.cal_date)
        .where(TradeCal.is_open.is_(True))
        .where(TradeCal.cal_date <= end)
        .order_by(TradeCal.cal_date.desc())
        .limit(n)
    )
    dates = list(session.execute(stmt).scalars().all())
    return sorted(dates)


def is_selectable(basic: StockBasic, trade_date: date, min_list_days: int = 60) -> tuple[bool, str]:
    """判断一只股票在 trade_date 是否可被选入候选池。

    过滤规则：
    1. ST / *ST 排除
    2. 上市不足 min_list_days 的次新排除
    3. 可扩展：退市风险 / 暂停上市等

    Returns:
        (是否可选, 被过滤原因)；可选时原因为空字符串
    """
    # ST 过滤：优先看 is_st 字段，兜底检查名称
    if basic.is_st:
        return False, "ST 股"
    if basic.name and ("ST" in basic.name.upper() or basic.name.startswith("*ST")):
        return False, "名称含 ST"

    # 次新过滤
    if basic.list_date:
        days_listed = (trade_date - basic.list_date).days
        if days_listed < min_list_days:
            return False, f"上市仅 {days_listed} 日，不足 {min_list_days} 日"

    return True, ""


def classify_market(ts_code: str) -> str:
    """根据 ts_code 判断板块：
    - 600xxx / 601xxx / 603xxx / 605xxx → 主板(沪)
    - 000xxx / 001xxx / 002xxx / 003xxx → 主板/中小板(深)
    - 300xxx / 301xxx → 创业板
    - 688xxx → 科创板
    - 8xxxxx / 4xxxxx → 北交所
    """
    code = ts_code.split(".")[0]
    prefix = code[:3]

    if prefix in {"600", "601", "603", "605"}:
        return "主板-沪"
    if prefix in {"000", "001", "002", "003"}:
        return "主板-深"
    if prefix in {"300", "301"}:
        return "创业板"
    if prefix == "688":
        return "科创板"
    if code.startswith("8") or code.startswith("4"):
        return "北交所"
    return "未知"
