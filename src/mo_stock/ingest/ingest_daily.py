"""每日行情数据采集：从 Tushare 拉取当日/历史数据并 upsert 到 PG。

**Phase 1 MVP 覆盖的表**：
- stock_basic（基础，周度刷新）
- trade_cal（交易日历，月度刷新）
- daily_kline（日线 OHLCV）
- daily_basic（换手 / PE / PB / 市值）
- limit_list（涨停列表）
- moneyflow（主力资金流向）

**Phase 2 会增加**：lhb、sw_daily、news_raw、anns_raw。

**使用**：
    ingestor = DailyIngestor()
    ingestor.ingest_one_day(date(2026, 4, 22))          # 每日增量
    ingestor.backfill(start=date(2025, 10, 22), end=date(2026, 4, 22))
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from mo_stock.data_sources.tushare_client import TushareClient, date_to_tushare
from mo_stock.storage import repo
from mo_stock.storage.db import get_session


class DailyIngestor:
    """每日数据采集器。"""

    def __init__(self, client: TushareClient | None = None) -> None:
        self.client = client or TushareClient()

    # ====================================================================
    # 基础数据：股票列表 / 交易日历（低频刷新）
    # ====================================================================

    def refresh_stock_basic(self) -> int:
        """刷新 stock_basic 表。周度或按需调用。

        Returns:
            upsert 行数
        """
        df = self.client.stock_basic(list_status="L")
        if df.empty:
            logger.warning("stock_basic 返回空")
            return 0

        rows = []
        for _, r in df.iterrows():
            name = str(r["name"])
            rows.append({
                "ts_code": r["ts_code"],
                "symbol": r["symbol"],
                "name": name,
                "area": r.get("area"),
                "industry": r.get("industry"),
                "sw_l1": None,  # 申万一级另外从 sw_member 补齐
                "list_date": _parse_date(r.get("list_date")),
                "is_st": _is_st(name),
            })

        with get_session() as s:
            n = repo.upsert_stock_basic(s, rows)
        logger.info("stock_basic upserted {} rows", n)
        return n

    def refresh_trade_cal(self, start: date, end: date) -> int:
        """刷新交易日历。start/end 为 Python date。"""
        df = self.client.trade_cal(date_to_tushare(start), date_to_tushare(end))
        if df.empty:
            return 0

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "cal_date": _parse_date(r["cal_date"]),
                "is_open": bool(r["is_open"]),
                "pretrade_date": _parse_date(r.get("pretrade_date")),
            })

        with get_session() as s:
            n = repo.upsert_trade_cal(s, rows)
        logger.info("trade_cal upserted {} rows", n)
        return n

    # ====================================================================
    # 日频数据：单日全市场
    # ====================================================================

    def ingest_daily_kline(self, trade_date: date) -> int:
        """拉当日全市场日线。"""
        df = self.client.daily(trade_date=date_to_tushare(trade_date))
        if df.empty:
            logger.warning("daily 返回空 ({})", trade_date)
            return 0

        rows = [
            {
                "ts_code": r["ts_code"],
                "trade_date": _parse_date(r["trade_date"]),
                "open": _nf(r.get("open")),
                "high": _nf(r.get("high")),
                "low": _nf(r.get("low")),
                "close": _nf(r.get("close")),
                "pre_close": _nf(r.get("pre_close")),
                "pct_chg": _nf(r.get("pct_chg")),
                "vol": _nf(r.get("vol")),
                "amount": _nf(r.get("amount")),
            }
            for _, r in df.iterrows()
        ]

        with get_session() as s:
            n = repo.upsert_daily_kline(s, rows)
        logger.info("daily_kline {} upserted {} rows", trade_date, n)
        return n

    def ingest_daily_basic(self, trade_date: date) -> int:
        """拉当日全市场基础指标（换手 / PE / PB / 市值）。"""
        df = self.client.daily_basic(trade_date=date_to_tushare(trade_date))
        if df.empty:
            return 0

        rows = [
            {
                "ts_code": r["ts_code"],
                "trade_date": _parse_date(r["trade_date"]),
                "turnover_rate": _nf(r.get("turnover_rate")),
                "turnover_rate_f": _nf(r.get("turnover_rate_f")),
                "volume_ratio": _nf(r.get("volume_ratio")),
                "pe_ttm": _nf(r.get("pe_ttm")),
                "pb": _nf(r.get("pb")),
                "total_mv": _nf(r.get("total_mv")),
                "circ_mv": _nf(r.get("circ_mv")),
            }
            for _, r in df.iterrows()
        ]

        with get_session() as s:
            n = repo.upsert_daily_basic(s, rows)
        logger.info("daily_basic {} upserted {} rows", trade_date, n)
        return n

    def ingest_limit_list(self, trade_date: date) -> int:
        """拉当日涨停列表（仅 U 类型）。"""
        df = self.client.limit_list_d(trade_date=date_to_tushare(trade_date), limit_type="U")
        if df.empty:
            logger.info("limit_list {} 当日无涨停", trade_date)
            return 0

        rows = [
            {
                "ts_code": r["ts_code"],
                "trade_date": _parse_date(r["trade_date"]),
                "limit_type": "U",
                "fd_amount": _nf(r.get("fd_amount")),
                "first_time": _str_or_none(r.get("first_time")),
                "last_time": _str_or_none(r.get("last_time")),
                "open_times": _ni(r.get("open_times")),
                "up_stat": _str_or_none(r.get("up_stat")),
                "limit_times": _ni(r.get("limit_times")),
            }
            for _, r in df.iterrows()
        ]

        with get_session() as s:
            n = repo.upsert_limit_list(s, rows)
        logger.info("limit_list {} upserted {} rows", trade_date, n)
        return n

    def ingest_moneyflow(self, trade_date: date) -> int:
        """拉当日全市场主力资金流向。"""
        df = self.client.moneyflow(trade_date=date_to_tushare(trade_date))
        if df.empty:
            return 0

        rows = [
            {
                "ts_code": r["ts_code"],
                "trade_date": _parse_date(r["trade_date"]),
                "net_mf_amount": _nf(r.get("net_mf_amount")),
                "buy_sm_amount": _nf(r.get("buy_sm_amount")),
                "sell_sm_amount": _nf(r.get("sell_sm_amount")),
                "buy_md_amount": _nf(r.get("buy_md_amount")),
                "sell_md_amount": _nf(r.get("sell_md_amount")),
                "buy_lg_amount": _nf(r.get("buy_lg_amount")),
                "sell_lg_amount": _nf(r.get("sell_lg_amount")),
                "buy_elg_amount": _nf(r.get("buy_elg_amount")),
                "sell_elg_amount": _nf(r.get("sell_elg_amount")),
            }
            for _, r in df.iterrows()
        ]

        with get_session() as s:
            n = repo.upsert_moneyflow(s, rows)
        logger.info("moneyflow {} upserted {} rows", trade_date, n)
        return n

    # ====================================================================
    # 组合入口
    # ====================================================================

    def ingest_one_day(self, trade_date: date) -> dict[str, int]:
        """拉取指定交易日的全部数据（MVP 范围）。"""
        logger.info("=== ingest_one_day {} ===", trade_date)
        stats = {
            "daily_kline": self.ingest_daily_kline(trade_date),
            "daily_basic": self.ingest_daily_basic(trade_date),
            "limit_list": self.ingest_limit_list(trade_date),
            "moneyflow": self.ingest_moneyflow(trade_date),
        }
        logger.info("ingest_one_day {} done: {}", trade_date, stats)
        return stats

    def backfill(self, start: date, end: date) -> dict[str, int]:
        """回填 [start, end] 区间的日频数据。

        按日逐天拉取，每天失败不影响整体（记录日志后继续）。
        """
        logger.info("=== backfill {} → {} ===", start, end)
        total: dict[str, int] = {}

        cursor = start
        while cursor <= end:
            try:
                stats = self.ingest_one_day(cursor)
                for k, v in stats.items():
                    total[k] = total.get(k, 0) + v
            except Exception as exc:  # noqa: BLE001
                logger.exception("ingest_one_day {} failed: {}", cursor, exc)
            cursor += timedelta(days=1)

        logger.info("backfill {} → {} done: {}", start, end, total)
        return total


# ------------------------------------------------------------------------
# 内部小工具：Tushare 字段值清洗
# ------------------------------------------------------------------------

def _parse_date(v: Any) -> date | None:
    """'20260422' 或 date 或 None → date | None。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s == "nan":
        return None
    parsed: date = pd.to_datetime(s).date()
    return parsed


def _nf(v: Any) -> float | None:
    """float or NaN/None → float | None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _ni(v: Any) -> int | None:
    """int or NaN/None → int | None。"""
    f = _nf(v)
    return int(f) if f is not None else None


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _is_st(name: str) -> bool:
    """根据股票名称判断是否 ST。"""
    if not name:
        return False
    upper = name.upper()
    return "ST" in upper or name.startswith("*ST")
