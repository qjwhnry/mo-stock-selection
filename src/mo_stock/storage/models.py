"""SQLAlchemy 2.x ORM 模型定义。

对应 plan §3 的 12 张表。设计原则：
- 原始数据表（kline / basic / limit / lhb / moneyflow / sw / news / anns）：180 天滚动
- 结果表（filter_score_daily / ai_analysis / selection_result）：永久保留供回测
- JSON 字段一律用 PG JSONB
- 时间字段用 timestamptz
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# ========================================================================
# 基础表：股票 / 交易日
# ========================================================================

class StockBasic(Base):
    """全 A 股基础信息，周度刷新。"""

    __tablename__ = "stock_basic"

    ts_code: Mapped[str] = mapped_column(String(12), primary_key=True)  # 600519.SH
    symbol: Mapped[str] = mapped_column(String(10), index=True)         # 600519
    name: Mapped[str] = mapped_column(String(50))
    area: Mapped[str | None] = mapped_column(String(20))
    industry: Mapped[str | None] = mapped_column(String(50), index=True)
    sw_l1: Mapped[str | None] = mapped_column(String(50), index=True)   # 申万一级
    list_date: Mapped[date | None] = mapped_column(Date)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TradeCal(Base):
    """交易日历。"""

    __tablename__ = "trade_cal"

    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, index=True)
    pretrade_date: Mapped[date | None] = mapped_column(Date)


# ========================================================================
# 行情数据：K 线 / 基础指标
# ========================================================================

class DailyKline(Base):
    """日线 OHLCV。复合主键 (ts_code, trade_date)。"""

    __tablename__ = "daily_kline"

    ts_code: Mapped[str] = mapped_column(String(12))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    pre_close: Mapped[float | None] = mapped_column(Float)
    pct_chg: Mapped[float | None] = mapped_column(Float)
    vol: Mapped[float | None] = mapped_column(Float)      # 手
    amount: Mapped[float | None] = mapped_column(Float)   # 千元

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_daily_kline_date", "trade_date"),
    )


class DailyBasic(Base):
    """每日基础指标：换手率 / PE / PB / 市值。"""

    __tablename__ = "daily_basic"

    ts_code: Mapped[str] = mapped_column(String(12))
    trade_date: Mapped[date] = mapped_column(Date)
    turnover_rate: Mapped[float | None] = mapped_column(Float)      # 换手率 %
    turnover_rate_f: Mapped[float | None] = mapped_column(Float)    # 自由流通换手
    volume_ratio: Mapped[float | None] = mapped_column(Float)       # 量比
    pe_ttm: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    total_mv: Mapped[float | None] = mapped_column(Float)           # 万元
    circ_mv: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_daily_basic_date", "trade_date"),
    )


# ========================================================================
# 异动数据：涨停 / 龙虎榜 / 资金流
# ========================================================================

class LimitList(Base):
    """涨跌停数据（Tushare limit_list_d）。"""

    __tablename__ = "limit_list"

    ts_code: Mapped[str] = mapped_column(String(12))
    trade_date: Mapped[date] = mapped_column(Date)
    limit_type: Mapped[str] = mapped_column(String(2), index=True)  # U 涨停 / D 跌停 / Z 炸板
    fd_amount: Mapped[float | None] = mapped_column(Float)          # 封单金额
    first_time: Mapped[str | None] = mapped_column(String(10))      # 首次封板 HH:MM:SS
    last_time: Mapped[str | None] = mapped_column(String(10))       # 最后封板
    open_times: Mapped[int | None] = mapped_column(Integer)         # 打开次数
    up_stat: Mapped[str | None] = mapped_column(String(20))         # 连板数统计 "2/3"
    limit_times: Mapped[int | None] = mapped_column(Integer)        # 连板数

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_limit_list_date", "trade_date"),
    )


class Lhb(Base):
    """龙虎榜上榜明细（Tushare top_list）。"""

    __tablename__ = "lhb"

    trade_date: Mapped[date] = mapped_column(Date)
    ts_code: Mapped[str] = mapped_column(String(12))
    name: Mapped[str | None] = mapped_column(String(50))
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)      # 总成交量（万元）
    l_sell: Mapped[float | None] = mapped_column(Float)      # 龙虎榜卖出额
    l_buy: Mapped[float | None] = mapped_column(Float)       # 龙虎榜买入额
    l_amount: Mapped[float | None] = mapped_column(Float)    # 龙虎榜成交额
    net_amount: Mapped[float | None] = mapped_column(Float)  # 净买入额
    reason: Mapped[str | None] = mapped_column(Text)         # 上榜理由
    seat: Mapped[dict | None] = mapped_column(JSONB)         # 席位明细 [{name, buy, sell, net}]

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code"),
        Index("ix_lhb_ts_code", "ts_code"),
    )


class Moneyflow(Base):
    """主力资金流向（Tushare moneyflow）。"""

    __tablename__ = "moneyflow"

    ts_code: Mapped[str] = mapped_column(String(12))
    trade_date: Mapped[date] = mapped_column(Date)
    net_mf_amount: Mapped[float | None] = mapped_column(Float)    # 主力净流入（万元）
    buy_sm_amount: Mapped[float | None] = mapped_column(Float)    # 小单
    sell_sm_amount: Mapped[float | None] = mapped_column(Float)
    buy_md_amount: Mapped[float | None] = mapped_column(Float)    # 中单
    sell_md_amount: Mapped[float | None] = mapped_column(Float)
    buy_lg_amount: Mapped[float | None] = mapped_column(Float)    # 大单
    sell_lg_amount: Mapped[float | None] = mapped_column(Float)
    buy_elg_amount: Mapped[float | None] = mapped_column(Float)   # 超大单
    sell_elg_amount: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_moneyflow_date", "trade_date"),
    )


# ========================================================================
# 板块数据
# ========================================================================

class SwDaily(Base):
    """申万板块日线（Tushare sw_daily）。"""

    __tablename__ = "sw_daily"

    sw_code: Mapped[str] = mapped_column(String(20))
    trade_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str | None] = mapped_column(String(50))
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    vol: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        PrimaryKeyConstraint("sw_code", "trade_date"),
        Index("ix_sw_daily_date", "trade_date"),
    )


# ========================================================================
# 情绪数据：新闻 / 公告 / 研报
# ========================================================================

class NewsRaw(Base):
    """新闻原文（Tushare major_news / news）。"""

    __tablename__ = "news_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str | None] = mapped_column(String(12), index=True)  # 可为 None 表示市场新闻
    pub_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    sentiment_score: Mapped[float | None] = mapped_column(Float)  # 词典打分结果，后续填充

    __table_args__ = (
        UniqueConstraint("title", "pub_time", name="uq_news_raw_title_time"),
    )


class AnnsRaw(Base):
    """公告原文（Tushare anns_d）。"""

    __tablename__ = "anns_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(12), index=True)
    ann_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(500))
    ann_type: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        UniqueConstraint("ts_code", "ann_date", "title", name="uq_anns_key"),
    )


class ResearchReport(Base):
    """研报（GTHT researchreport skill）。"""

    __tablename__ = "research_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(12), index=True)
    pub_date: Mapped[date] = mapped_column(Date, index=True)
    org: Mapped[str | None] = mapped_column(String(100))       # 研究机构
    title: Mapped[str] = mapped_column(String(500))
    rating: Mapped[str | None] = mapped_column(String(20))     # 买入 / 增持 / 中性
    tp_low: Mapped[float | None] = mapped_column(Float)        # 目标价下限
    tp_high: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSONB)            # GTHT 返回的完整 JSON

    __table_args__ = (
        UniqueConstraint("ts_code", "pub_date", "org", "title", name="uq_report_key"),
    )


# ========================================================================
# 结果表：规则分 / AI 分析 / 最终选股（永久保留）
# ========================================================================

class FilterScoreDaily(Base):
    """规则层 5 维度各自打分，逐行存储便于回测。"""

    __tablename__ = "filter_score_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    ts_code: Mapped[str] = mapped_column(String(12), index=True)
    dim: Mapped[str] = mapped_column(String(20), index=True)  # limit / moneyflow / lhb / sector / sentiment
    score: Mapped[float] = mapped_column(Float)               # 0–100
    detail: Mapped[dict | None] = mapped_column(JSONB)        # 本维度打分细节，供报告和复盘

    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", "dim", name="uq_filter_score_key"),
        Index("ix_filter_score_date_dim", "trade_date", "dim"),
    )


class AiAnalysis(Base):
    """Claude AI 分析结果（Phase 3 启用）。"""

    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    ts_code: Mapped[str] = mapped_column(String(12), index=True)
    ai_score: Mapped[int] = mapped_column(Integer)
    thesis: Mapped[str] = mapped_column(Text)
    key_catalysts: Mapped[list | None] = mapped_column(JSONB)
    risks: Mapped[list | None] = mapped_column(JSONB)
    suggested_entry: Mapped[str | None] = mapped_column(String(100))
    stop_loss: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", name="uq_ai_analysis_key"),
    )


class SelectionResult(Base):
    """最终选股结果：每日 TOP N 候选股。"""

    __tablename__ = "selection_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    ts_code: Mapped[str] = mapped_column(String(12), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    rule_score: Mapped[float] = mapped_column(Numeric(5, 2))
    ai_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    final_score: Mapped[float] = mapped_column(Numeric(5, 2))
    picked: Mapped[bool] = mapped_column(Boolean, default=True)
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", name="uq_selection_key"),
        Index("ix_selection_date_rank", "trade_date", "rank"),
    )
