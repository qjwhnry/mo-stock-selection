"""涨停相关共享工具函数。

供 LimitFilter 和 MoneyflowFilter 共同使用，避免 MoneyflowFilter 间接依赖 LimitFilter。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.storage.models import LimitList


def parse_limit_times(limit_times: int | None, up_stat: str | None) -> int:
    """从 limit_times 或 up_stat 解析连板数。

    Tushare 某些日期 limit_times 可能为空但 up_stat 有值（如 "2/3"），
    需要同时查两个字段做 fallback。
    """
    if limit_times:
        return limit_times
    if up_stat and "/" in up_stat:
        try:
            return int(up_stat.split("/")[0])
        except (ValueError, IndexError):
            pass
    return 1


def get_prev_limit_times_map(
    session: Session, trade_date: date,
) -> dict[str, int]:
    """批量获取指定交易日涨停股的连板数。

    返回 {ts_code: limit_times}，一次查询全量，避免在循环内逐股查询。
    """
    rows = session.execute(
        select(LimitList.ts_code, LimitList.limit_times, LimitList.up_stat)
        .where(LimitList.trade_date == trade_date)
        .where(LimitList.limit_type == "U")
    ).all()
    return {r[0]: parse_limit_times(r[1], r[2]) for r in rows}
