"""SQLAlchemy 2.x ORM 模型定义。

对应 plan §3 的 12 张表。设计原则：
- 原始数据表（kline / basic / limit / lhb / moneyflow / sw / news / anns）：180 天滚动
- 结果表（filter_score_daily / ai_analysis / selection_result）：永久保留供回测
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
    seat: Mapped[dict | None] = mapped_column(
        JSONB, comment="席位明细 JSON：[{name, buy, sell, net}, ...]",
    )

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code"),
        Index("ix_lhb_ts_code", "ts_code"),
        {"comment": "龙虎榜（Tushare top_list + top_inst），LhbFilter 打分源"},
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
# 结果表：规则分 / AI 分析 / 最终选股（永久保留）
# ========================================================================

class FilterScoreDaily(Base):
    """规则层 5 维度各自打分，逐行存储便于回测。"""

    __tablename__ = "filter_score_daily"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="评分对应的交易日")
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
        UniqueConstraint("trade_date", "ts_code", "dim", name="uq_filter_score_key"),
        Index("ix_filter_score_date_dim", "trade_date", "dim"),
        {"comment": "规则层 5 维度逐行打分表（永久保留，供回测）"},
    )


class AiAnalysis(Base):
    """Claude AI 分析结果（Phase 3 启用）。"""

    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="分析对应的交易日")
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
        UniqueConstraint("trade_date", "ts_code", name="uq_ai_analysis_key"),
        {"comment": "Claude AI 分析结果（Phase 3 启用），永久保留"},
    )


class SelectionResult(Base):
    """最终选股结果：每日 TOP N 候选股。"""

    __tablename__ = "selection_result"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="选股目标交易日")
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    rank: Mapped[int] = mapped_column(
        Integer, comment="TOP N 排名（1 最强）；未入选填 0",
    )
    rule_score: Mapped[float] = mapped_column(Numeric(5, 2), comment="规则层综合分 0-100")
    ai_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), comment="AI 层综合分 0-100；Phase 3 前为 NULL",
    )
    final_score: Mapped[float] = mapped_column(
        Numeric(5, 2),
        comment="最终分；Phase 1 = rule_score，Phase 3 起 = rule*0.6 + ai*0.4",
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
        UniqueConstraint("trade_date", "ts_code", name="uq_selection_key"),
        Index("ix_selection_date_rank", "trade_date", "rank"),
        {"comment": "最终选股 TOP N 结果（永久保留，作为每日报告与回测的主数据源）"},
    )
