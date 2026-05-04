"""Pydantic response schema——API 返回结构定义。"""
from __future__ import annotations

from pydantic import BaseModel


class ReportListItem(BaseModel):
    trade_date: str
    strategy: str
    count: int
    avg_score: float
    max_score: float


class ReportListResponse(BaseModel):
    items: list[ReportListItem]
    total: int
    page: int
    page_size: int


class IndexSnapshot(BaseModel):
    close: float
    pct_chg: float


class MarketData(BaseModel):
    sh_index: IndexSnapshot
    hs300_index: IndexSnapshot
    regime_score: float


class StockItem(BaseModel):
    rank: int
    ts_code: str
    name: str
    industry: str
    final_score: float
    rule_score: float
    ai_score: float | None = None
    scores: dict[str, int]
    ai_summary: str | None = None
    picked: bool


class ReportDetailResponse(BaseModel):
    trade_date: str
    strategy: str
    market: MarketData
    stocks: list[StockItem]
    available_sectors: list[str]


class AiAnalysisData(BaseModel):
    thesis: str
    key_catalysts: list[str] | None = None
    risks: list[str] | None = None
    suggested_entry: str | None = None
    stop_loss: str | None = None


class RecentPick(BaseModel):
    trade_date: str
    picked: bool
    final_score: float


class StockDetailResponse(BaseModel):
    ts_code: str
    name: str
    industry: str
    latest_scores: dict[str, int]
    ai_analysis: AiAnalysisData | None = None
    recent_picks: list[RecentPick]


class MoneyflowSummaryStats(BaseModel):
    net_mf_positive_count: int
    total_net_mf_wan: float | None = None


class MoneyflowSummaryItem(BaseModel):
    ts_code: str
    name: str
    industry: str | None = None
    close: float | None = None
    pct_chg: float | None = None
    net_mf_wan: float | None = None
    net_mf_ratio_pct: float | None = None
    buy_lg_wan: float | None = None
    sell_lg_wan: float | None = None
    buy_elg_wan: float | None = None
    sell_elg_wan: float | None = None
    picked: bool
    rule_score: float | None = None
    final_score: float | None = None
    scores: dict[str, int]


class MoneyflowSummaryResponse(BaseModel):
    items: list[MoneyflowSummaryItem]
    total: int
    page: int
    page_size: int
    summary: MoneyflowSummaryStats


class LhbSummaryStats(BaseModel):
    lhb_count: int
    institution_net_buy_count: int
    total_lhb_net_amount_wan: float | None = None


class LhbSummaryItem(BaseModel):
    ts_code: str
    name: str
    industry: str | None = None
    close: float | None = None
    pct_chg: float | None = None
    lhb_buy_wan: float | None = None
    lhb_sell_wan: float | None = None
    lhb_amount_wan: float | None = None
    lhb_net_amount_wan: float | None = None
    lhb_net_rate_pct: float | None = None
    lhb_amount_rate_pct: float | None = None
    reason: str | None = None
    seat_summary: dict[str, int]
    picked: bool
    rule_score: float | None = None
    final_score: float | None = None
    scores: dict[str, int]


class LhbSummaryResponse(BaseModel):
    items: list[LhbSummaryItem]
    total: int
    page: int
    page_size: int
    summary: LhbSummaryStats


class SectorListResponse(BaseModel):
    trade_date: str
    sectors: list[str]


class StockKlineSignal(BaseModel):
    trade_date: str
    close: float | None = None
    pct_chg: float | None = None
    amount: float | None = None


class StockMoneyflowSignal(BaseModel):
    trade_date: str
    net_mf_wan: float | None = None
    net_mf_ratio_pct: float | None = None
    buy_lg_wan: float | None = None
    sell_lg_wan: float | None = None
    buy_elg_wan: float | None = None
    sell_elg_wan: float | None = None


class StockLhbSignal(BaseModel):
    trade_date: str
    lhb_net_amount_wan: float | None = None
    lhb_net_rate_pct: float | None = None
    reason: str | None = None


class StockScoreSignal(BaseModel):
    trade_date: str
    dim: str
    score: float
    detail: dict | None = None


class StockSelectionSignal(BaseModel):
    trade_date: str
    picked: bool
    rule_score: float | None = None
    final_score: float | None = None


class StockSignalsResponse(BaseModel):
    ts_code: str
    name: str | None = None
    industry: str | None = None
    kline: list[StockKlineSignal]
    moneyflow: list[StockMoneyflowSignal]
    lhb: list[StockLhbSignal]
    scores: list[StockScoreSignal]
    selections: list[StockSelectionSignal]


class LhbSeatItem(BaseModel):
    seat_no: int
    exalter: str | None = None
    side: str | None = None
    buy_wan: float | None = None
    sell_wan: float | None = None
    net_buy_wan: float | None = None
    seat_type: str
    reason: str | None = None


class LhbSeatsResponse(BaseModel):
    trade_date: str
    ts_code: str
    seats: list[LhbSeatItem]


class RunTaskRequest(BaseModel):
    strategy: str = "short"
    trade_date: str | None = None
    skip_ai: bool = False
    force: bool = False


class TaskStatusResponse(BaseModel):
    task_id: str | None = None
    status: str  # running / idle / error
    strategy: str | None = None
    trade_date: str | None = None
    started_at: str | None = None
    error: str | None = None


class SchedulerConfig(BaseModel):
    strategy: str = "short"
    skip_ai: bool = False
    cron_hour: int = 15
    cron_minute: int = 30


class SchedulerStatusResponse(BaseModel):
    status: str  # running / stopped
    strategy: str | None = None
    cron: str | None = None
    next_run: str | None = None
