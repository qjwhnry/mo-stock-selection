"""SQLAlchemy 2.x ORM 模型定义。

覆盖行情、基础资料、题材/行业、龙虎榜席位、AI 分析、选股结果和波段持仓等表。
设计原则：
- 高频原始数据表按运行任务做滚动保留；结果表和回测/持仓表按策略维度长期保留
- 短线 / 波段共用结果表，通过 `strategy` 隔离；波段持仓通过 `mode` 和
  `backtest_run_id` 隔离回测与实盘
- JSON 字段一律用 PG JSONB
- 时间字段用 timestamptz
- **所有字段/表都带 comment**，执行 `create_all()` 或 `mo-stock apply-comments` 后
  可用 `\\d+ <table>` 在 psql 里查看中文说明
"""
from __future__ import annotations

from datetime import UTC, date, datetime

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

    ts_code: Mapped[str] = mapped_column(
        String(12), primary_key=True,
        comment="Tushare 股票代码，形如 600519.SH / 000001.SZ",
    )
    symbol: Mapped[str] = mapped_column(
        String(10), index=True,
        comment="纯数字代码，形如 600519",
    )
    name: Mapped[str] = mapped_column(String(50), comment="股票简称")
    area: Mapped[str | None] = mapped_column(String(20), comment="所在省份")
    industry: Mapped[str | None] = mapped_column(
        String(50), index=True,
        comment="Tushare 行业分类（较粗）",
    )
    sw_l1: Mapped[str | None] = mapped_column(
        String(50), index=True,
        comment="申万一级行业名称（用于板块维度匹配）",
    )
    list_date: Mapped[date | None] = mapped_column(
        Date, comment="上市日期；硬规则 min_list_days 依赖此字段过滤次新",
    )
    is_st: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True,
        comment="是否 ST / *ST；硬规则 exclude_st 依赖此字段",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="本行最近一次从 Tushare 刷新的时间",
    )

    __table_args__ = ({"comment": "A 股股票基础信息（名称、行业、上市日、ST 标记），周度刷新"},)


class TradeCal(Base):
    """交易日历。"""

    __tablename__ = "trade_cal"

    cal_date: Mapped[date] = mapped_column(
        Date, primary_key=True, comment="自然日日期",
    )
    is_open: Mapped[bool] = mapped_column(
        Boolean, index=True,
        comment="是否交易日：True=开市、False=休市",
    )
    pretrade_date: Mapped[date | None] = mapped_column(
        Date, comment="对应的上一个交易日（节假日后拿到的是节前最后一个交易日）",
    )

    __table_args__ = ({"comment": "交易日历；调度器判断是否交易日 / 硬规则回看 N 日时依赖此表"},)


# ========================================================================
# 行情数据：K 线 / 基础指标
# ========================================================================

class DailyKline(Base):
    """日线 OHLCV。复合主键 (ts_code, trade_date)。"""

    __tablename__ = "daily_kline"

    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    open: Mapped[float | None] = mapped_column(Float, comment="开盘价")
    high: Mapped[float | None] = mapped_column(Float, comment="最高价")
    low: Mapped[float | None] = mapped_column(Float, comment="最低价")
    close: Mapped[float | None] = mapped_column(Float, comment="收盘价（前复权前原始价）")
    pre_close: Mapped[float | None] = mapped_column(Float, comment="昨收盘价")
    pct_chg: Mapped[float | None] = mapped_column(
        Float, comment="涨跌幅（%）= (close-pre_close)/pre_close*100",
    )
    vol: Mapped[float | None] = mapped_column(Float, comment="成交量（手，1 手=100 股）")
    amount: Mapped[float | None] = mapped_column(Float, comment="成交额（千元）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_daily_kline_date", "trade_date"),
        {"comment": "日线 OHLCV 行情（Tushare daily），180 天滚动保留"},
    )


class DailyBasic(Base):
    """每日基础指标：换手率 / PE / PB / 市值。"""

    __tablename__ = "daily_basic"

    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    turnover_rate: Mapped[float | None] = mapped_column(
        Float, comment="换手率（%）= 成交量/总股本",
    )
    turnover_rate_f: Mapped[float | None] = mapped_column(
        Float, comment="换手率（自由流通股，%）；更能反映筹码活跃度",
    )
    volume_ratio: Mapped[float | None] = mapped_column(
        Float, comment="量比 = 当日分时均量/过去 5 日分时均量；>1 代表放量",
    )
    pe_ttm: Mapped[float | None] = mapped_column(
        Float, comment="市盈率 TTM（滚动 12 月净利润），亏损股为空",
    )
    pb: Mapped[float | None] = mapped_column(Float, comment="市净率 = 总市值/净资产")
    total_mv: Mapped[float | None] = mapped_column(Float, comment="总市值（万元）")
    circ_mv: Mapped[float | None] = mapped_column(Float, comment="流通市值（万元）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_daily_basic_date", "trade_date"),
        {"comment": "每日基础指标（Tushare daily_basic）：换手率 / PE / PB / 市值，180 天滚动"},
    )


# ========================================================================
# 异动数据：涨停 / 龙虎榜 / 资金流
# ========================================================================

class LimitList(Base):
    """涨跌停数据（Tushare limit_list_d）。"""

    __tablename__ = "limit_list"

    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    limit_type: Mapped[str] = mapped_column(
        String(2), index=True,
        comment="涨跌停类型：U 涨停 / D 跌停 / Z 炸板",
    )
    fd_amount: Mapped[float | None] = mapped_column(
        Float, comment="封单金额（元）；封单越大、越难打开",
    )
    first_time: Mapped[str | None] = mapped_column(
        String(10), comment="首次封板时间 HH:MM:SS；越早越强势",
    )
    last_time: Mapped[str | None] = mapped_column(
        String(10), comment="最后封板时间 HH:MM:SS",
    )
    open_times: Mapped[int | None] = mapped_column(
        Integer, comment="打开次数；≥2 次在 LimitFilter 里一票否决",
    )
    up_stat: Mapped[str | None] = mapped_column(
        String(20), comment="连板统计，形如 '2/3'（近 2 连板、累计上榜 3 次）",
    )
    limit_times: Mapped[int | None] = mapped_column(Integer, comment="连板数")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_limit_list_date", "trade_date"),
        {"comment": "涨停 / 跌停 / 炸板明细（Tushare limit_list_d），LimitFilter 打分源"},
    )


class Lhb(Base):
    """龙虎榜上榜明细（Tushare top_list）。"""

    __tablename__ = "lhb"

    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    name: Mapped[str | None] = mapped_column(String(50), comment="股票名称")
    close: Mapped[float | None] = mapped_column(Float, comment="当日收盘价")
    pct_change: Mapped[float | None] = mapped_column(Float, comment="当日涨跌幅（%）")
    turnover_rate: Mapped[float | None] = mapped_column(Float, comment="换手率（%）")
    amount: Mapped[float | None] = mapped_column(Float, comment="总成交额（元，Tushare 实际返回科学计数法 E7）")
    l_sell: Mapped[float | None] = mapped_column(Float, comment="龙虎榜席位卖出额（元）")
    l_buy: Mapped[float | None] = mapped_column(Float, comment="龙虎榜席位买入额（元）")
    l_amount: Mapped[float | None] = mapped_column(Float, comment="龙虎榜席位成交总额（元）")
    net_amount: Mapped[float | None] = mapped_column(
        Float, comment="席位净买入额（元）= l_buy - l_sell；为正代表游资/机构加仓",
    )
    net_rate: Mapped[float | None] = mapped_column(
        Float,
        comment="龙虎榜净买入占当日总成交比例（%）= net_amount/amount×100；跨股可比，"
                "LhbFilter 用此字段做净额分档（替代绝对金额，避免大盘股偏弱、小盘股偏强）",
    )
    amount_rate: Mapped[float | None] = mapped_column(
        Float,
        comment="龙虎榜成交占当日总成交比例（%）= l_amount/amount×100；反映席位主导度，"
                "LhbFilter 用此字段做纯度分档",
    )
    reason: Mapped[str | None] = mapped_column(
        Text, comment="上榜原因（如：日涨幅偏离值达 7%、日价格涨幅偏离值达 7% 的前三只证券）",
    )
    # v2.1：seat JSONB 已移除，席位明细搬到独立表 lhb_seat_detail（参见模型末尾）

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code"),
        Index("ix_lhb_ts_code", "ts_code"),
        {"comment": "龙虎榜汇总（Tushare top_list），席位明细见 lhb_seat_detail"},
    )


class Moneyflow(Base):
    """主力资金流向（Tushare moneyflow）。"""

    __tablename__ = "moneyflow"

    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    net_mf_amount: Mapped[float | None] = mapped_column(
        Float, comment="主力净流入额（万元）= 大单+超大单净流入；MoneyflowFilter 主要依据",
    )
    buy_sm_amount: Mapped[float | None] = mapped_column(
        Float, comment="小单买入额（万元，≤5 万元）",
    )
    sell_sm_amount: Mapped[float | None] = mapped_column(Float, comment="小单卖出额（万元）")
    buy_md_amount: Mapped[float | None] = mapped_column(
        Float, comment="中单买入额（万元，5 万-20 万）",
    )
    sell_md_amount: Mapped[float | None] = mapped_column(Float, comment="中单卖出额（万元）")
    buy_lg_amount: Mapped[float | None] = mapped_column(
        Float, comment="大单买入额（万元，20 万-100 万）",
    )
    sell_lg_amount: Mapped[float | None] = mapped_column(Float, comment="大单卖出额（万元）")
    buy_elg_amount: Mapped[float | None] = mapped_column(
        Float, comment="超大单买入额（万元，>100 万）",
    )
    sell_elg_amount: Mapped[float | None] = mapped_column(Float, comment="超大单卖出额（万元）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_moneyflow_date", "trade_date"),
        {"comment": "主力资金流向（Tushare moneyflow），MoneyflowFilter 打分源"},
    )


# ========================================================================
# 板块数据
# ========================================================================

class SwDaily(Base):
    """申万板块日线（Tushare sw_daily，doc_id=327）。"""

    __tablename__ = "sw_daily"

    sw_code: Mapped[str] = mapped_column(
        String(20), comment="申万行业代码（如 801080.SI）",
    )
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称")
    open: Mapped[float | None] = mapped_column(Float, comment="板块开盘点位")
    high: Mapped[float | None] = mapped_column(Float, comment="板块最高点位")
    low: Mapped[float | None] = mapped_column(Float, comment="板块最低点位")
    close: Mapped[float | None] = mapped_column(Float, comment="板块收盘点位")
    change: Mapped[float | None] = mapped_column(Float, comment="板块涨跌点位（close - 前日close）")
    pct_change: Mapped[float | None] = mapped_column(
        Float, comment="板块涨跌幅（%）；板块维度打分的主要输入",
    )
    vol: Mapped[float | None] = mapped_column(Float, comment="板块总成交量（万股）")
    amount: Mapped[float | None] = mapped_column(Float, comment="板块总成交额（万元）")
    pe: Mapped[float | None] = mapped_column(Float, comment="板块市盈率（整体法）")
    pb: Mapped[float | None] = mapped_column(Float, comment="板块市净率（整体法）")
    float_mv: Mapped[float | None] = mapped_column(Float, comment="板块流通市值（万元）")
    total_mv: Mapped[float | None] = mapped_column(Float, comment="板块总市值（万元）")

    __table_args__ = (
        PrimaryKeyConstraint("sw_code", "trade_date"),
        Index("ix_sw_daily_date", "trade_date"),
        {"comment": "申万一级行业板块日线（Tushare sw_daily），SectorFilter 打分源"},
    )


class IndexMember(Base):
    """股票 → 申万行业归属映射（Tushare index_member_all，doc_id=335）。

    申万行业分类一股归属唯一（同一时刻 1 只股 → 1 个三级行业，归属树状到二级、一级）。
    所以 PK 用 ts_code 即可。每月按需刷新（拉 is_new='Y' 的最新成分），
    板块归属调整时 upsert 自动覆盖旧记录。
    SectorFilter 通过此表把 sw_daily.pct_change 关联到具体股票。
    """

    __tablename__ = "index_member"

    ts_code: Mapped[str] = mapped_column(
        String(12), primary_key=True, comment="股票代码",
    )
    l1_code: Mapped[str | None] = mapped_column(
        String(20), index=True, comment="申万一级行业代码（如 801080.SI）",
    )
    l1_name: Mapped[str | None] = mapped_column(String(50), comment="一级行业名称")
    l2_code: Mapped[str | None] = mapped_column(String(20), comment="二级行业代码")
    l2_name: Mapped[str | None] = mapped_column(String(50), comment="二级行业名称")
    l3_code: Mapped[str | None] = mapped_column(String(20), comment="三级行业代码")
    l3_name: Mapped[str | None] = mapped_column(String(50), comment="三级行业名称")
    in_date: Mapped[date | None] = mapped_column(Date, comment="纳入该三级行业的日期")

    __table_args__ = (
        {"comment": "股票→申万行业映射；sector_filter 据此关联 sw_daily 拿到板块涨幅"},
    )


class ThsIndex(Base):
    """同花顺概念板块元数据（Tushare ths_index，doc_id=259）。

    存所有 A 股概念板块（type='N'）的代码、名称、成分数。月度刷新。
    与 stock_basic 不同：这里 ts_code 是**概念代码**（如 885328.TI），不是个股代码。
    """

    __tablename__ = "ths_index"

    ts_code: Mapped[str] = mapped_column(
        String(20), primary_key=True,
        comment="同花顺概念代码（如 885328.TI）",
    )
    name: Mapped[str | None] = mapped_column(String(50), comment="概念名称（如「新能源车」）")
    count: Mapped[int | None] = mapped_column(Integer, comment="概念成分股数")
    exchange: Mapped[str | None] = mapped_column(
        String(10), comment="交易所：A=A股 / HK=港股 / US=美股",
    )
    list_date: Mapped[date | None] = mapped_column(Date, comment="概念上市日")
    type: Mapped[str | None] = mapped_column(
        String(5), comment="N=概念 / I=行业 / R=地域 / S=特色 / ST=风格 / TH=主题 / BB=宽基",
    )

    __table_args__ = (
        {"comment": "同花顺概念板块元数据（Tushare ths_index）；sector_filter 题材命中加分用"},
    )


class ThsMember(Base):
    """同花顺概念成分（Tushare ths_member，doc_id=261）。

    一股可属多个概念（多对多）。PK = (ts_code 概念, con_code 股票)。
    sector_filter 用此表回答「这只股是不是落在了今天的热点概念里」。
    注意：weight / in_date / out_date 接口当前暂无数据但保留字段供将来扩展。
    """

    __tablename__ = "ths_member"

    ts_code: Mapped[str] = mapped_column(String(20), comment="同花顺概念代码")
    con_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    con_name: Mapped[str | None] = mapped_column(String(50), comment="股票名称（冗余存储减少 join）")
    weight: Mapped[float | None] = mapped_column(Float, comment="权重（接口暂无数据，预留）")
    in_date: Mapped[date | None] = mapped_column(Date, comment="纳入日期（接口暂无数据，预留）")
    out_date: Mapped[date | None] = mapped_column(Date, comment="剔除日期（接口暂无数据，预留）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "con_code"),
        {"comment": "同花顺概念成分（Tushare ths_member）；股票→概念多对多映射"},
    )


# ========================================================================
# 情绪数据：新闻 / 公告 / 研报
# ========================================================================

class NewsRaw(Base):
    """新闻原文（Tushare major_news / news）。"""

    __tablename__ = "news_raw"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    ts_code: Mapped[str | None] = mapped_column(
        String(12), index=True,
        comment="关联股票代码；NULL 表示全市场新闻（如政策、行业新闻）",
    )
    pub_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True,
        comment="发布时间（timestamptz）",
    )
    title: Mapped[str] = mapped_column(String(500), comment="新闻标题")
    content: Mapped[str | None] = mapped_column(Text, comment="新闻正文，可能较长")
    source: Mapped[str | None] = mapped_column(String(100), comment="来源媒体")
    sentiment_score: Mapped[float | None] = mapped_column(
        Float, comment="情绪得分（词典法打分结果，后续填充，-1~1）",
    )

    __table_args__ = (
        UniqueConstraint("title", "pub_time", name="uq_news_raw_title_time"),
        {"comment": "原始新闻（Tushare news / major_news），SentimentFilter 打分源"},
    )


class AnnsRaw(Base):
    """公告原文（Tushare anns_d）。"""

    __tablename__ = "anns_raw"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    ann_date: Mapped[date] = mapped_column(Date, index=True, comment="公告日期")
    title: Mapped[str] = mapped_column(
        String(500), comment="公告标题；硬规则负面关键词匹配作用于此字段",
    )
    url: Mapped[str | None] = mapped_column(String(500), comment="公告 PDF 或详情页 URL")
    ann_type: Mapped[str | None] = mapped_column(
        String(50), comment="公告类型（定期报告 / 重大事项 / 股东会等）",
    )

    __table_args__ = (
        UniqueConstraint("ts_code", "ann_date", "title", name="uq_anns_key"),
        {"comment": "上市公司公告（Tushare anns_d），硬规则负面关键词匹配源"},
    )


class ResearchReport(Base):
    """研报（GTHT researchreport skill）。"""

    __tablename__ = "research_report"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    pub_date: Mapped[date] = mapped_column(Date, index=True, comment="研报发布日期")
    org: Mapped[str | None] = mapped_column(String(100), comment="研究机构名称")
    title: Mapped[str] = mapped_column(String(500), comment="研报标题")
    rating: Mapped[str | None] = mapped_column(
        String(20), comment="评级：买入 / 增持 / 中性 / 减持 / 卖出",
    )
    tp_low: Mapped[float | None] = mapped_column(Float, comment="目标价下限（元）")
    tp_high: Mapped[float | None] = mapped_column(Float, comment="目标价上限（元）")
    raw: Mapped[dict | None] = mapped_column(
        JSONB, comment="GTHT skill 返回的完整 JSON，保留字段便于回溯",
    )

    __table_args__ = (
        UniqueConstraint("ts_code", "pub_date", "org", "title", name="uq_report_key"),
        {"comment": "券商研报（国泰海通 GTHT skill），情绪维度辅助输入"},
    )


# ========================================================================
# 结果表：规则分 / AI 分析 / 最终选股 / 波段持仓（按策略或模式隔离）
# ========================================================================

class FilterScoreDaily(Base):
    """规则层各维度逐行打分，按 strategy 隔离，便于报告与回测复盘。"""

    __tablename__ = "filter_score_daily"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="评分对应的交易日")
    strategy: Mapped[str] = mapped_column(
        String(20),
        default="short",
        index=True,
        comment="策略标识：short / swing",
    )
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    dim: Mapped[str] = mapped_column(
        String(20), index=True,
        comment="维度标识：limit / moneyflow / lhb / sector / sentiment",
    )
    score: Mapped[float] = mapped_column(Float, comment="本维度得分 0-100")
    detail: Mapped[dict | None] = mapped_column(
        JSONB, comment="本维度打分细节 JSON，供报告展示和复盘",
    )

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "strategy", "ts_code", "dim",
            name="uq_filter_score_key",
        ),
        Index("ix_filter_score_date_strategy_dim", "trade_date", "strategy", "dim"),
        {"comment": "规则层维度逐行打分表（按 strategy 隔离，永久保留供回测）"},
    )


class AiAnalysis(Base):
    """Claude AI 分析结果，按 strategy 隔离。"""

    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="分析对应的交易日")
    strategy: Mapped[str] = mapped_column(
        String(20),
        default="short",
        index=True,
        comment="策略标识：short / swing",
    )
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    ai_score: Mapped[int] = mapped_column(Integer, comment="AI 给出的 0-100 综合分")
    thesis: Mapped[str] = mapped_column(Text, comment="AI 的投资逻辑核心陈述")
    key_catalysts: Mapped[list | None] = mapped_column(
        JSONB, comment="关键催化剂列表（JSON 数组字符串）",
    )
    risks: Mapped[list | None] = mapped_column(JSONB, comment="风险提示列表（JSON 数组字符串）")
    suggested_entry: Mapped[str | None] = mapped_column(
        String(100), comment="建议入场价 / 区间，如 '50-52 元'",
    )
    stop_loss: Mapped[str | None] = mapped_column(
        String(100), comment="止损位，如 '跌破 48 元止损'",
    )
    model: Mapped[str | None] = mapped_column(
        String(50), comment="使用的 Claude 模型 ID，如 claude-sonnet-4-6",
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer, comment="本次调用输入 token 数")
    output_tokens: Mapped[int | None] = mapped_column(Integer, comment="本次调用输出 token 数")
    cache_creation_tokens: Mapped[int | None] = mapped_column(
        Integer, comment="prompt cache 写入 token 数（首次）",
    )
    cache_read_tokens: Mapped[int | None] = mapped_column(
        Integer, comment="prompt cache 命中读取 token 数（后续）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="分析记录创建时间",
    )

    __table_args__ = (
        UniqueConstraint("trade_date", "strategy", "ts_code", name="uq_ai_analysis_key"),
        Index("ix_ai_analysis_date_strategy", "trade_date", "strategy"),
        {"comment": "Claude AI 分析结果（按 strategy 隔离），永久保留"},
    )


class SelectionResult(Base):
    """最终选股结果：每日 TOP N 候选股。"""

    __tablename__ = "selection_result"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="选股目标交易日")
    strategy: Mapped[str] = mapped_column(
        String(20),
        default="short",
        index=True,
        comment="策略标识：short / swing",
    )
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    rank: Mapped[int] = mapped_column(
        Integer, comment="TOP N 排名（1 最强）；未入选填 0",
    )
    rule_score: Mapped[float] = mapped_column(Numeric(5, 2), comment="规则层综合分 0-100")
    ai_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), comment="AI 层综合分 0-100；跳过 AI 或调用失败时为 NULL",
    )
    final_score: Mapped[float] = mapped_column(
        Numeric(5, 2),
        comment="最终分；AI 缺失时等于 rule_score，否则按综合层权重融合 rule_score 与 ai_score",
    )
    picked: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否入选 TOP N（未通过硬规则或超出排名为 False）",
    )
    reject_reason: Mapped[str | None] = mapped_column(
        String(200), comment="硬规则淘汰原因；picked=True 时为 NULL",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="入库时间",
    )

    __table_args__ = (
        UniqueConstraint("trade_date", "strategy", "ts_code", name="uq_selection_key"),
        Index("ix_selection_date_strategy_rank", "trade_date", "strategy", "rank"),
        {"comment": "最终选股 TOP N 结果（按 strategy 隔离，作为报告与回测主数据源）"},
    )


class SwingPosition(Base):
    """波段策略持仓状态跟踪（回测 + 实盘共用，通过 mode 隔离）。"""

    __tablename__ = "swing_position"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    mode: Mapped[str] = mapped_column(
        String(20), index=True, comment="运行模式：backtest / live",
    )
    backtest_run_id: Mapped[str | None] = mapped_column(
        String(36), index=True,
        comment="回测批次 ID（mode=backtest 时填写，如 UUID）；live 时为 NULL",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="记录日期")
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    status: Mapped[str] = mapped_column(
        String(20), comment="持仓状态：watching / holding / stopped / exited",
    )
    entry_price: Mapped[float | None] = mapped_column(Float, comment="入场价")
    entry_date: Mapped[date | None] = mapped_column(Date, comment="入场日期")
    stop_loss_price: Mapped[float | None] = mapped_column(
        Float, comment="当前止损价；watching 状态可为空",
    )
    target_price: Mapped[float | None] = mapped_column(Float, comment="目标价")
    atr_at_entry: Mapped[float | None] = mapped_column(Float, comment="入场时 ATR(20)")
    max_price: Mapped[float | None] = mapped_column(Float, comment="持仓期最高价（移动止盈用）")
    pnl_pct: Mapped[float | None] = mapped_column(Float, comment="当前浮动盈亏 %")
    exit_reason: Mapped[str | None] = mapped_column(String(50), comment="退出原因")
    holding_days: Mapped[int | None] = mapped_column(Integer, comment="持仓交易日数")
    detail: Mapped[dict | None] = mapped_column(JSONB, comment="补充信息")

    __table_args__ = (
        Index("ix_swing_pos_mode_date", "mode", "trade_date"),
        Index("ix_swing_pos_run_id", "backtest_run_id"),
        {"comment": "波段持仓跟踪（回测/实盘通过 mode 隔离，回测用 backtest_run_id 批次管理）"},
    )


# ========================================================================
# 题材增强表：同花顺概念行情 / 涨停概念 / 概念资金流
# ========================================================================

class ThsDaily(Base):
    """同花顺概念/行业指数日行情（Tushare ths_daily, doc_id=260）。"""

    __tablename__ = "ths_daily"

    ts_code: Mapped[str] = mapped_column(String(20), comment="同花顺板块代码，如 885806.TI")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称（冗余 ths_index）")
    close: Mapped[float | None] = mapped_column(Float, comment="收盘点位")
    open: Mapped[float | None] = mapped_column(Float, comment="开盘点位")
    high: Mapped[float | None] = mapped_column(Float, comment="最高")
    low: Mapped[float | None] = mapped_column(Float, comment="最低")
    pre_close: Mapped[float | None] = mapped_column(Float, comment="昨收")
    avg_price: Mapped[float | None] = mapped_column(Float, comment="平均价")
    change: Mapped[float | None] = mapped_column(Float, comment="涨跌额")
    pct_change: Mapped[float | None] = mapped_column(Float, comment="涨跌幅（%）")
    vol: Mapped[float | None] = mapped_column(Float, comment="成交量")
    turnover_rate: Mapped[float | None] = mapped_column(Float, comment="换手率（%）")
    total_mv: Mapped[float | None] = mapped_column(Float, comment="总市值")
    float_mv: Mapped[float | None] = mapped_column(Float, comment="流通市值")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_ths_daily_date_pct", "trade_date", "pct_change"),
        {"comment": "同花顺概念/行业指数日行情，ThemeFilter 强度输入"},
    )


class LimitConceptDaily(Base):
    """涨停最强概念板块统计（Tushare limit_cpt_list, doc_id=357）。"""

    __tablename__ = "limit_concept_daily"

    ts_code: Mapped[str] = mapped_column(String(20), comment="板块代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称")
    days: Mapped[int | None] = mapped_column(Integer, comment="上榜天数")
    up_stat: Mapped[str | None] = mapped_column(String(50), comment="连板高度描述")
    cons_nums: Mapped[int | None] = mapped_column(Integer, comment="连板家数")
    up_nums: Mapped[int | None] = mapped_column(Integer, comment="涨停家数")
    pct_chg: Mapped[float | None] = mapped_column(Float, comment="概念涨跌幅（%）")
    rank: Mapped[int | None] = mapped_column(Integer, comment="热点排名，1 最强")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_limit_concept_date_rank", "trade_date", "rank"),
        {"comment": "每日涨停最强概念板块，短线题材热度输入"},
    )


class ThsConceptMoneyflow(Base):
    """同花顺概念板块每日资金流向（Tushare moneyflow_cnt_ths, doc_id=371）。"""

    __tablename__ = "ths_concept_moneyflow"

    ts_code: Mapped[str] = mapped_column(String(20), comment="概念板块代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称")
    lead_stock: Mapped[str | None] = mapped_column(String(50), comment="领涨股票名称")
    pct_change: Mapped[float | None] = mapped_column(Float, comment="板块涨跌幅（%）")
    company_num: Mapped[int | None] = mapped_column(Integer, comment="成分公司数量")
    pct_change_stock: Mapped[float | None] = mapped_column(Float, comment="领涨股涨跌幅（%）")
    net_buy_amount: Mapped[float | None] = mapped_column(Float, comment="买入额（亿元）")
    net_sell_amount: Mapped[float | None] = mapped_column(Float, comment="卖出额（亿元）")
    net_amount: Mapped[float | None] = mapped_column(Float, comment="净流入额（亿元）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_concept_moneyflow_date_net", "trade_date", "net_amount"),
        {"comment": "概念板块资金流向，ThemeFilter 资金确认输入"},
    )


# ========================================================================
# 龙虎榜席位明细 + 游资名录
# ========================================================================

class HotMoneyList(Base):
    """游资名录（Tushare hm_list, doc_id=311）。"""

    __tablename__ = "hot_money_list"

    name: Mapped[str] = mapped_column(
        String(100), primary_key=True,
        comment="游资名称（Tushare 主键，如「赵老哥」）",
    )
    desc: Mapped[str | None] = mapped_column(Text, comment="游资风格说明")
    orgs: Mapped[str | None] = mapped_column(
        Text, comment="关联营业部，分号/逗号分隔的原始字符串",
    )

    __table_args__ = ({"comment": "Tushare 游资名录，用于龙虎榜席位身份识别"},)


class HotMoneyDetail(Base):
    """游资每日交易明细（Tushare hm_detail, doc_id=312）。数据从 2022-08 起。

    当前表按 (trade_date, ts_code, hm_name) 聚合存储；同一游资涉及多个营业部时，
    ingest 层会把营业部合并到 hm_orgs（用 ; 分隔），金额字段按游资维度聚合。
    hm_orgs 非主键字段，缺失时用空串 '' 兜底，便于展示和复盘。
    """

    __tablename__ = "hot_money_detail"

    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    hm_name: Mapped[str] = mapped_column(String(100), comment="游资名称")
    hm_orgs: Mapped[str] = mapped_column(
        String(200), default="",
        comment="关联营业部（聚合展示字段；缺失用空串兜底）",
    )
    ts_name: Mapped[str | None] = mapped_column(String(50), comment="股票名称")
    buy_amount: Mapped[float | None] = mapped_column(Float, comment="买入金额（元）")
    sell_amount: Mapped[float | None] = mapped_column(Float, comment="卖出金额（元）")
    net_amount: Mapped[float | None] = mapped_column(Float, comment="净买卖金额（元）")
    tag: Mapped[str | None] = mapped_column(String(50), comment="标签（Tushare 原始）")

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code", "hm_name"),
        Index("ix_hot_money_detail_date", "trade_date"),
        Index("ix_hot_money_detail_hm", "hm_name", "trade_date"),
        {"comment": "每日游资交易明细（按 date×stock×游资 聚合，营业部用 ; 拼接到 hm_orgs）"},
    )


class LhbSeatDetail(Base):
    """龙虎榜席位明细（Tushare top_inst）。

    替代 Lhb.seat JSONB，便于 SQL 直接过滤"今天机构净买 > N 的票"。
    主键为 (trade_date, ts_code, seat_key)，其中 seat_key 是 sha1 内容哈希，
    避免 top_inst 返回顺序变化导致重跑覆盖错行。
    """

    __tablename__ = "lhb_seat_detail"

    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    seat_key: Mapped[str] = mapped_column(
        String(64),
        comment="稳定席位键：sha1(ts_code|exalter|side|reason)，避免重跑时 top_inst 返回顺序变化导致覆盖错行",
    )
    seat_no: Mapped[int] = mapped_column(
        Integer,
        comment="展示序号 1-N，按稳定排序（reason,side,exalter,buy,sell,net_buy）后生成。"
                "**消费方排序请用 seat_no**——seat_key 是 sha1 hash，顺序无业务含义",
    )
    exalter: Mapped[str | None] = mapped_column(String(200), comment="席位/营业部名称")
    side: Mapped[str | None] = mapped_column(String(2), comment="0=买榜 / 1=卖榜（仅指榜单位置）")
    buy: Mapped[float | None] = mapped_column(Float, comment="买入金额（元）")
    sell: Mapped[float | None] = mapped_column(Float, comment="卖出金额（元）")
    net_buy: Mapped[float | None] = mapped_column(Float, comment="净买卖金额（元）")
    reason: Mapped[str | None] = mapped_column(String(100), comment="上榜原因")
    seat_type: Mapped[str] = mapped_column(
        String(20),
        comment="ingest 时分类：institution / northbound / hot_money / other",
    )

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code", "seat_key"),
        Index("ix_lhb_seat_date_type", "trade_date", "seat_type"),
        Index("ix_lhb_seat_date_code", "trade_date", "ts_code"),
        {"comment": "龙虎榜席位明细（v2.1 替代 Lhb.seat JSONB），LhbFilter 席位结构打分输入"},
    )
