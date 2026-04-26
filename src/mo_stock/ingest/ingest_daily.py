"""每日行情数据采集：从 Tushare 拉取当日/历史数据并 upsert 到 PG。

**Phase 1 MVP 覆盖的表**：
- stock_basic（基础，周度刷新）
- trade_cal（交易日历，月度刷新）
- daily_kline（日线 OHLCV）
- daily_basic（换手 / PE / PB / 市值）
- limit_list（涨停列表）
- moneyflow（主力资金流向）
- lhb（龙虎榜上榜股，仅 top_list；席位明细 top_inst 待 Phase 2）
- sw_daily（申万一级板块日线）

**Phase 2 会增加**：top_inst（lhb 席位明细 → seat 字段）、news_raw、anns_raw。

**使用**：
    ingestor = DailyIngestor()
    ingestor.ingest_one_day(date(2026, 4, 22))          # 每日增量
    ingestor.backfill(start=date(2025, 10, 22), end=date(2026, 4, 22))
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from mo_stock.data_sources.tushare_client import TushareClient, date_to_tushare
from mo_stock.storage import repo
from mo_stock.storage.db import get_session
from mo_stock.storage.models import HotMoneyList, ThsIndex


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

    def refresh_index_member(self) -> int:
        """刷新申万行业成分（index_member 表）。月度或按需调用。

        全市场约 5400 行；接口单次上限 2000 行，所以按申万一级行业循环分页。
        一级行业列表用 index_classify(L1, SW2021) 取（约 31 个），不依赖
        index_member_all 的截断 bootstrap（之前那种方式只能看到前 2~3 个 l1 行业）。
        """
        classify_df = self.client.index_classify(level="L1", src="SW2021")
        if classify_df.empty:
            logger.warning("index_classify L1 SW2021 返回空，无法继续")
            return 0

        l1_codes = sorted(classify_df["index_code"].dropna().unique().tolist())
        logger.info("index_member 准备按 {} 个一级行业分页拉取", len(l1_codes))

        all_rows: list[dict[str, Any]] = []
        for l1 in l1_codes:
            df = self.client.index_member_all(l1_code=l1, is_new="Y")
            rows = _index_member_rows_from_df(df)
            all_rows.extend(rows)

        if not all_rows:
            logger.warning("index_member 全部 l1 拉取后无有效数据")
            return 0

        # 跨 l1 final dedupe：申万 2026-03-05 评审等行业调整时，Tushare 旧归属漏标
        # out_date，导致同一只股出现在多个 l1（如 600185 房地产↔商贸零售）。按 in_date
        # 最新优先去重 = 跟到申万最近一次评审的归属。
        deduped = _dedupe_keep_latest_in_date(all_rows)
        cross_dups = len(all_rows) - len(deduped)
        if cross_dups:
            logger.info("跨 l1 dedupe: {} → {}（去掉 {} 条跨一级行业的重复股，保留 in_date 最新）",
                        len(all_rows), len(deduped), cross_dups)

        with get_session() as s:
            n = repo.upsert_index_member(s, deduped)
        logger.info("index_member upserted {} rows", n)
        return n

    def refresh_ths_concept(self) -> tuple[int, int]:
        """刷新同花顺概念板块（ths_index 元数据 + ths_member 成分）。

        流程：
        1. 拉 ths_index(type='N', exchange='A') 拿全部 A 股概念板块（约 408 个）
        2. 循环每个概念调 ths_member(ts_code=...) 拉成分股
        3. upsert 到 ths_index + ths_member 两张表

        耗时：408 次接口调用 × 节流 ≈ 3-4 分钟。
        积分要求：6000；调用频率上限 200 次/分钟。
        商业用途注意：同花顺数据有版权，需联系同花顺授权。

        Returns:
            (concepts_upserted, members_upserted)
        """
        index_df = self.client.ths_index(type="N", exchange="A")
        if index_df.empty:
            logger.warning("ths_index type=N exchange=A 返回空")
            return (0, 0)

        index_rows = _ths_index_rows_from_df(index_df)
        with get_session() as s:
            n_idx = repo.upsert_ths_index(s, index_rows)
        logger.info("ths_index upserted {} 个概念", n_idx)

        # 按概念循环拉成分
        all_member_rows: list[dict[str, Any]] = []
        for i, concept_code in enumerate(sorted(index_df["ts_code"].dropna().unique().tolist()), 1):
            df = self.client.ths_member(ts_code=concept_code)
            rows = _ths_member_rows_from_df(df)
            all_member_rows.extend(rows)
            if i % 50 == 0:
                logger.info("ths_member 进度 {}/{}, 累计 {} 条成分", i, n_idx, len(all_member_rows))

        if not all_member_rows:
            logger.warning("ths_member 全部概念拉取后无有效数据")
            return (n_idx, 0)

        with get_session() as s:
            n_mem = repo.upsert_ths_member(s, all_member_rows)
        logger.info("ths_member upserted {} 条成分（涵盖 {} 个概念）", n_mem, n_idx)
        return (n_idx, n_mem)

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

    def ingest_lhb(self, trade_date: date) -> int:
        """拉当日龙虎榜上榜股（top_list）。无上榜数据时静默返回 0。"""
        df = self.client.top_list(trade_date=date_to_tushare(trade_date))
        rows = _lhb_rows_from_df(df)
        if not rows:
            logger.info("lhb {} 当日无龙虎榜数据", trade_date)
            return 0

        with get_session() as s:
            n = repo.upsert_lhb(s, rows)
        logger.info("lhb {} upserted {} rows", trade_date, n)
        return n

    def ingest_sw_daily(self, trade_date: date) -> int:
        """拉当日全市场申万板块日线（sw_daily）。"""
        df = self.client.sw_daily(trade_date=date_to_tushare(trade_date))
        rows = _sw_daily_rows_from_df(df)
        if not rows:
            logger.warning("sw_daily 返回空 ({})", trade_date)
            return 0

        with get_session() as s:
            n = repo.upsert_sw_daily(s, rows)
        logger.info("sw_daily {} upserted {} rows", trade_date, n)
        return n

    # ====================================================================
    # v2.1 plan Task 3：题材增强 + 龙虎榜席位 ingest 方法
    # ====================================================================

    def ingest_ths_daily(self, trade_date: date) -> int:
        """拉当日同花顺概念/行业指数日行情。"""
        df = self.client.ths_daily(trade_date=date_to_tushare(trade_date))
        if df.empty:
            logger.warning("ths_daily {} 返回空", trade_date)
            return 0
        with get_session() as s:
            name_map = {x.ts_code: x.name for x in s.query(ThsIndex).all()}
            rows = _ths_daily_rows_from_df(df, name_map=name_map)
            n = repo.upsert_ths_daily(s, rows)
        logger.info("ths_daily {} upserted {} rows", trade_date, n)
        return n

    def ingest_limit_concept(self, trade_date: date) -> int:
        """拉当日涨停最强概念榜单。"""
        df = self.client.limit_cpt_list(trade_date=date_to_tushare(trade_date))
        rows = _limit_concept_rows_from_df(df)
        if not rows:
            logger.info("limit_cpt_list {} 返回空", trade_date)
            return 0
        with get_session() as s:
            n = repo.upsert_limit_concept_daily(s, rows)
        logger.info("limit_concept_daily {} upserted {} rows", trade_date, n)
        return n

    def ingest_concept_moneyflow(self, trade_date: date) -> int:
        """拉当日同花顺概念板块资金流向。"""
        df = self.client.moneyflow_cnt_ths(trade_date=date_to_tushare(trade_date))
        rows = _concept_moneyflow_rows_from_df(df)
        if not rows:
            return 0
        with get_session() as s:
            n = repo.upsert_concept_moneyflow(s, rows)
        logger.info("ths_concept_moneyflow {} upserted {} rows", trade_date, n)
        return n

    def ingest_top_inst(self, trade_date: date) -> int:
        """拉当日龙虎榜席位明细（top_inst → LhbSeatDetail）。

        依赖 hot_money_list（用 set 加速识别游资席位）。若 hm_list 为空，所有
        席位会被打成 'other'，记 warning 提示先跑 refresh-basics --with-hm-list。
        """
        df = self.client.top_inst(trade_date=date_to_tushare(trade_date))
        if df.empty:
            return 0
        with get_session() as s:
            hot_money_orgs: set[str] = set()
            for hm in s.query(HotMoneyList).all():
                hot_money_orgs |= _split_orgs_string(hm.orgs)
            if not hot_money_orgs:
                logger.warning(
                    "hot_money_list 为空，top_inst 席位仍入库但 hot_money 分类会降级为 other；"
                    "请先跑 refresh-basics --with-hm-list",
                )
            rows = _top_inst_rows_from_df(df, hot_money_orgs=hot_money_orgs)
            n = repo.upsert_lhb_seat_detail(s, rows)
        logger.info("lhb_seat_detail {} upserted {} rows", trade_date, n)
        return n

    def ingest_hm_detail(self, trade_date: date) -> int:
        """拉当日游资交易明细（hm_detail → HotMoneyDetail）。"""
        df = self.client.hm_detail(trade_date=date_to_tushare(trade_date))
        rows = _hm_detail_rows_from_df(df)
        if not rows:
            return 0
        with get_session() as s:
            n = repo.upsert_hot_money_detail(s, rows)
        logger.info("hm_detail {} upserted {} rows", trade_date, n)
        return n

    def refresh_hot_money_list(self) -> int:
        """刷新游资名录（低频元数据，refresh-basics 调用）。"""
        df = self.client.hm_list()
        rows = _hm_list_rows_from_df(df)
        if not rows:
            logger.warning("hm_list 返回空")
            return 0
        with get_session() as s:
            n = repo.upsert_hot_money_list(s, rows)
        logger.info("hot_money_list upserted {} rows", n)
        return n

    # ====================================================================
    # 组合入口（v2.1：CORE / ENHANCED 分组）
    # ====================================================================

    def ingest_one_day(
        self, trade_date: date, *, skip_enhanced: bool = False,
    ) -> dict[str, int]:
        """拉取指定交易日的全部数据。

        - **CORE 6 步**（必跑）：daily_kline / daily_basic / limit_list / moneyflow
          / lhb / sw_daily
        - **ENHANCED 5 步**（v2.1 新增，可 skip）：ths_daily / limit_concept /
          concept_moneyflow / top_inst / hm_detail

        每个 ingest_xxx 单独 try/except：单步失败只影响该步，不阻断同日其他维度。
        stats 中 -1 表示该步抛错。

        Args:
            trade_date: 目标交易日
            skip_enhanced: True 时只跑 6 个 CORE 步骤（调试或 Tushare 限速时）
        """
        logger.info(
            "=== ingest_one_day {} (skip_enhanced={}) ===",
            trade_date, skip_enhanced,
        )

        core_steps: list[tuple[str, Callable[[date], int]]] = [
            ("daily_kline", self.ingest_daily_kline),
            ("daily_basic", self.ingest_daily_basic),
            ("limit_list", self.ingest_limit_list),
            ("moneyflow", self.ingest_moneyflow),
            ("lhb", self.ingest_lhb),
            ("sw_daily", self.ingest_sw_daily),
        ]
        enhanced_steps: list[tuple[str, Callable[[date], int]]] = [
            ("ths_daily", self.ingest_ths_daily),
            ("limit_concept", self.ingest_limit_concept),
            ("concept_moneyflow", self.ingest_concept_moneyflow),
            ("top_inst", self.ingest_top_inst),
            ("hm_detail", self.ingest_hm_detail),
        ]
        steps = core_steps if skip_enhanced else core_steps + enhanced_steps

        stats: dict[str, int] = {}
        for name, fn in steps:
            try:
                stats[name] = fn(trade_date)
            except Exception as exc:  # noqa: BLE001
                # -1 标记失败步骤；完整堆栈走 logger.exception
                stats[name] = -1
                logger.exception("{} {} failed: {}", name, trade_date, exc)

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
    """'20260422' / 'YYYY-MM-DD' / date / None → date | None。

    P1-12：显式按 'YYYYMMDD' 解析（Tushare 全部接口的日期标准格式），
    避免 pandas 模糊推断带来的时区/格式漂移。失败时尝试 'YYYY-MM-DD'
    兜底（Tushare 部分新接口返回带连字符的日期），都失败再 raise。
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    if not s or s == "nan":
        return None
    # Tushare 主流格式 'YYYYMMDD'
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        pass
    # 兜底：'YYYY-MM-DD' 或 ISO 格式
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"_parse_date: 无法解析日期字符串 {s!r}（期望 YYYYMMDD 或 YYYY-MM-DD）") from exc


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


def _lhb_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare top_list DataFrame → Lhb 表 row dict 列表。

    入库字段：trade_date, ts_code, name, close, pct_change, turnover_rate,
    amount, l_sell, l_buy, l_amount, net_amount, **net_rate, amount_rate**, reason。
    seat（席位明细）需要 top_inst 单独拉取，当前留 None；float_values 不入库。

    去重：top_list 对同一只股同一天可能因不同上榜原因返回多行，而 Lhb 表 PK
    是 (trade_date, ts_code)，必须按主键去重，否则 ON CONFLICT 撞 PK 抛
    CardinalityViolation。保留首行。
    """
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="first")
    return [
        {
            "trade_date": _parse_date(r["trade_date"]),
            "ts_code": r["ts_code"],
            "name": _str_or_none(r.get("name")),
            "close": _nf(r.get("close")),
            "pct_change": _nf(r.get("pct_change")),
            "turnover_rate": _nf(r.get("turnover_rate")),
            "amount": _nf(r.get("amount")),
            "l_sell": _nf(r.get("l_sell")),
            "l_buy": _nf(r.get("l_buy")),
            "l_amount": _nf(r.get("l_amount")),
            "net_amount": _nf(r.get("net_amount")),
            "net_rate": _nf(r.get("net_rate")),
            "amount_rate": _nf(r.get("amount_rate")),
            "reason": _str_or_none(r.get("reason")),
            "seat": None,  # 待 top_inst 接入后填充
        }
        for _, r in df.iterrows()
    ]


def _dedupe_keep_latest_in_date(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 ts_code 去重，每个 ts_code 保留 in_date 最新的一条（None 视为最旧）。

    用途：refresh_index_member 跨 31 个一级行业拼接后，少数股可能因 Tushare 漏标
    out_date 而出现在多个 l1（如 600185 房地产↔商贸零售）。取 in_date 最新 = 跟到
    申万最近一次评审的归属，自动抹平 Tushare 数据滞后。

    输入顺序无关；输出顺序未定义（不影响 upsert）。
    """
    sorted_rows = sorted(
        rows,
        key=lambda r: r.get("in_date") or date.min,  # None → date.min，排在最后
        reverse=True,
    )
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in sorted_rows:
        if row["ts_code"] not in seen:
            seen.add(row["ts_code"])
            deduped.append(row)
    return deduped


def _ths_index_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare ths_index → ThsIndex 表 row dict 列表。"""
    if df.empty:
        return []
    return [
        {
            "ts_code": r["ts_code"],
            "name": _str_or_none(r.get("name")),
            "count": _ni(r.get("count")),
            "exchange": _str_or_none(r.get("exchange")),
            "list_date": _parse_date(r.get("list_date")),
            "type": _str_or_none(r.get("type")),
        }
        for _, r in df.iterrows()
    ]


def _ths_member_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare ths_member → ThsMember 表 row dict 列表。

    两步去噪：
    1. filter is_new='Y' 保留当前最新成分
    2. drop_duplicates by (ts_code, con_code) 联合主键，防御镜像复制 bug
    """
    if df.empty:
        return []
    df = df[df["is_new"] == "Y"]
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["ts_code", "con_code"], keep="first")
    return [
        {
            "ts_code": r["ts_code"],
            "con_code": r["con_code"],
            "con_name": _str_or_none(r.get("con_name")),
            "weight": _nf(r.get("weight")),
            "in_date": _parse_date(r.get("in_date")),
            "out_date": _parse_date(r.get("out_date")),
        }
        for _, r in df.iterrows()
    ]


def _index_member_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare index_member_all DataFrame → IndexMember 表 row dict 列表。

    申万行业分类一股一行业，PK=ts_code。两步去噪：
    1. 先 filter is_new='Y'（保留当前最新归属，过滤已被剔除的历史记录）
    2. 再 drop_duplicates by ts_code（防御镜像 bug：某些 Tushare 镜像会把同行
       复制多份返回，会撞 ON CONFLICT DO UPDATE 的 PK）

    out_date / is_new / name 三字段不入库（前者一直 NULL，is_new 一直 'Y'，
    股票名 stock_basic 已有）。
    """
    if df.empty:
        return []
    df = df[df["is_new"] == "Y"]
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["ts_code"], keep="first")
    return [
        {
            "ts_code": r["ts_code"],
            "l1_code": _str_or_none(r.get("l1_code")),
            "l1_name": _str_or_none(r.get("l1_name")),
            "l2_code": _str_or_none(r.get("l2_code")),
            "l2_name": _str_or_none(r.get("l2_name")),
            "l3_code": _str_or_none(r.get("l3_code")),
            "l3_name": _str_or_none(r.get("l3_name")),
            "in_date": _parse_date(r.get("in_date")),
        }
        for _, r in df.iterrows()
    ]


def _sw_daily_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare sw_daily DataFrame → SwDaily 表 row dict 列表。

    字段对齐 Tushare 官方接口（doc_id=327）：
    - 重命名：ts_code → sw_code（模型主键）
    - 字段全集：name, open, high, low, close, change, pct_change, vol, amount,
      pe, pb, float_mv, total_mv
    - 注意：sw_daily 接口**没有** turnover_rate 字段（不要再映射）
    """
    if df.empty:
        return []
    return [
        {
            "sw_code": r["ts_code"],  # 重命名：tushare ts_code → 模型 sw_code
            "trade_date": _parse_date(r["trade_date"]),
            "name": _str_or_none(r.get("name")),
            "open": _nf(r.get("open")),
            "high": _nf(r.get("high")),
            "low": _nf(r.get("low")),
            "close": _nf(r.get("close")),
            "change": _nf(r.get("change")),
            "pct_change": _nf(r.get("pct_change")),
            "vol": _nf(r.get("vol")),
            "amount": _nf(r.get("amount")),
            "pe": _nf(r.get("pe")),
            "pb": _nf(r.get("pb")),
            "float_mv": _nf(r.get("float_mv")),
            "total_mv": _nf(r.get("total_mv")),
        }
        for _, r in df.iterrows()
    ]


# ============================================================================
# v2.1 plan Task 3：题材增强 + 龙虎榜席位明细 清洗函数
# ============================================================================

def _ths_daily_rows_from_df(
    df: pd.DataFrame, name_map: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Tushare ths_daily DataFrame → ThsDaily row dict 列表。

    name_map 由调用方从 ths_index 表注入，避免每次都 join 拿名字。
    """
    if df.empty:
        return []
    name_map = name_map or {}
    return [
        {
            "ts_code": r["ts_code"],
            "trade_date": _parse_date(r["trade_date"]),
            "name": name_map.get(r["ts_code"]),
            "close": _nf(r.get("close")),
            "open": _nf(r.get("open")),
            "high": _nf(r.get("high")),
            "low": _nf(r.get("low")),
            "pre_close": _nf(r.get("pre_close")),
            "avg_price": _nf(r.get("avg_price")),
            "change": _nf(r.get("change")),
            "pct_change": _nf(r.get("pct_change")),
            "vol": _nf(r.get("vol")),
            "turnover_rate": _nf(r.get("turnover_rate")),
            "total_mv": _nf(r.get("total_mv")),
            "float_mv": _nf(r.get("float_mv")),
        }
        for _, r in df.iterrows()
    ]


def _limit_concept_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare limit_cpt_list → LimitConceptDaily row dict 列表。

    防 Tushare 镜像 bug：drop_duplicates by (trade_date, ts_code) 主键，keep first。
    """
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="first")
    return [
        {
            "ts_code": r["ts_code"],
            "trade_date": _parse_date(r["trade_date"]),
            "name": _str_or_none(r.get("name")),
            "days": _ni(r.get("days")),
            "up_stat": _str_or_none(r.get("up_stat")),
            "cons_nums": _ni(r.get("cons_nums")),
            "up_nums": _ni(r.get("up_nums")),
            "pct_chg": _nf(r.get("pct_chg")),
            "rank": _ni(r.get("rank")),
        }
        for _, r in df.iterrows()
    ]


def _concept_moneyflow_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare moneyflow_cnt_ths → ThsConceptMoneyflow row dict 列表。

    v2.1 修法：3 个净额字段（net_buy_amount / net_sell_amount / net_amount）
    全部入库，与表定义对齐。
    """
    if df.empty:
        return []
    return [
        {
            "ts_code": r["ts_code"],
            "trade_date": _parse_date(r["trade_date"]),
            "name": _str_or_none(r.get("name")),
            "lead_stock": _str_or_none(r.get("lead_stock")),
            "pct_change": _nf(r.get("pct_change")),
            "company_num": _ni(r.get("company_num")),
            "pct_change_stock": _nf(r.get("pct_change_stock")),
            "net_buy_amount": _nf(r.get("net_buy_amount")),
            "net_sell_amount": _nf(r.get("net_sell_amount")),
            "net_amount": _nf(r.get("net_amount")),
        }
        for _, r in df.iterrows()
    ]


def _hm_list_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare hm_list → HotMoneyList row dict 列表。"""
    if df.empty:
        return []
    return [
        {
            "name": r["name"],
            "desc": _str_or_none(r.get("desc")),
            "orgs": _str_or_none(r.get("orgs")),
        }
        for _, r in df.iterrows()
    ]


def _hm_detail_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Tushare hm_detail → HotMoneyDetail row dict 列表。

    PK = (trade_date, ts_code, hm_name)。Tushare 同股+同游资+同日可能因不同
    营业部返回多行，必须 dedupe 防 PG ON CONFLICT 撞重复键。保留首行。
    """
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["trade_date", "ts_code", "hm_name"], keep="first")
    return [
        {
            "trade_date": _parse_date(r["trade_date"]),
            "ts_code": r["ts_code"],
            "hm_name": r["hm_name"],
            "ts_name": _str_or_none(r.get("ts_name")),
            "buy_amount": _nf(r.get("buy_amount")),
            "sell_amount": _nf(r.get("sell_amount")),
            "net_amount": _nf(r.get("net_amount")),
            "hm_orgs": _str_or_none(r.get("hm_orgs")),
            "tag": _str_or_none(r.get("tag")),
        }
        for _, r in df.iterrows()
    ]


# ---- 龙虎榜席位明细 + 游资识别 ----

_ORGS_SEP_RE = re.compile(r"[,;，；]")


def _split_orgs_string(orgs: str | None) -> set[str]:
    """从 hm_list.orgs 拆出营业部名 set，去 None/空白。

    格式如 "中信证券上海溧阳路营业部, 华泰证券深圳益田路营业部;..."。
    用 set 是为了完全相等匹配，避免子串误判（如 "中信证券" 命中所有中信席位）。
    """
    if not orgs:
        return set()
    return {p.strip() for p in _ORGS_SEP_RE.split(orgs) if p.strip()}


def _classify_seat(exalter: str | None, hot_money_orgs: set[str]) -> str:
    """席位身份分类（v2.1 简化版，无 quant_like）。

    返回值 ∈ {institution, northbound, hot_money, other}。
    无 "quant_like"——v1 启发式（"华鑫证券" → quant_like）会误报。
    """
    name = (exalter or "").strip()
    if not name:
        return "other"
    if "机构专用" in name:
        return "institution"
    if "沪股通专用" in name or "深股通专用" in name:
        return "northbound"
    if name in hot_money_orgs:
        return "hot_money"
    return "other"


def _stable_seat_key(
    ts_code: str, exalter: str | None, side: str | None, reason: str | None,
) -> str:
    """生成稳定席位键 = sha1(ts_code|exalter|side|reason)。

    v2.1 关键设计：用内容哈希而非 top_inst 返回顺序做主键，避免重跑时
    "(date, ts_code, 1)" 这条从机构被覆盖成游资的脏数据漂移。
    """
    raw = "|".join([ts_code, exalter or "", side or "", reason or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _top_inst_rows_from_df(
    df: pd.DataFrame, hot_money_orgs: set[str],
) -> list[dict[str, Any]]:
    """Tushare top_inst → LhbSeatDetail row dict 列表。

    PK = (trade_date, ts_code, seat_key)；seat_no 仅展示，按稳定排序生成 1..N。
    稳定排序键：(reason, side, exalter, buy, sell, net_buy)——保证不同会话
    repo.get_lhb_seats_today 返回顺序一致，便于报告与回归对照。
    """
    if df.empty:
        return []
    sort_cols = ["trade_date", "ts_code", "reason", "side", "exalter", "buy", "sell", "net_buy"]
    df_sorted = df.sort_values(
        [c for c in sort_cols if c in df.columns], na_position="last",
    )
    # 防 sha1 冲撞：(trade_date, ts_code, exalter, side, reason) 完全相同就 dedupe
    dedupe_cols = [c for c in ["trade_date", "ts_code", "exalter", "side", "reason"]
                   if c in df_sorted.columns]
    df_sorted = df_sorted.drop_duplicates(subset=dedupe_cols, keep="first")
    rows: list[dict[str, Any]] = []
    counter: dict[tuple[date | None, str], int] = {}
    for _, r in df_sorted.iterrows():
        td = _parse_date(r["trade_date"])
        ts = r["ts_code"]
        key = (td, ts)
        counter[key] = counter.get(key, 0) + 1
        exalter = _str_or_none(r.get("exalter"))
        side = _str_or_none(r.get("side"))
        reason = _str_or_none(r.get("reason"))
        rows.append({
            "trade_date": td,
            "ts_code": ts,
            "seat_key": _stable_seat_key(ts, exalter, side, reason),
            "seat_no": counter[key],
            "exalter": exalter,
            "side": side,
            "buy": _nf(r.get("buy")),
            "sell": _nf(r.get("sell")),
            "net_buy": _nf(r.get("net_buy")),
            "reason": reason,
            "seat_type": _classify_seat(exalter, hot_money_orgs),
        })
    return rows
