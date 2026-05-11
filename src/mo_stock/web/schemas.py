"""Pydantic response schema——API 返回结构定义。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator


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
    concepts: list[str] = []
    concept_count: int = 0
    final_score: float
    rule_score: float
    ai_score: float | None = None
    scores: dict[str, int]
    score_details: dict[str, dict] = {}
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
    forward_return_5d: float | None = None
    forward_return_10d: float | None = None


class StockDetailResponse(BaseModel):
    ts_code: str
    name: str
    industry: str
    concepts: list[str] = []
    concept_count: int = 0
    latest_scores: dict[str, int]
    score_details: dict[str, dict] = {}
    ai_score: float | None = None
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
    skip_enhanced: bool = False
    force: bool = False


class SyncOneDayTaskRequest(BaseModel):
    trade_date: str | None = None
    skip_enhanced: bool = False


class BackfillTaskRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=730)
    end_date: str | None = None


class RefreshIndexTaskRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=1825)
    end_date: str | None = None


class RefreshMetadataTaskRequest(BaseModel):
    refresh_basics: bool = True
    with_ths: bool = False
    with_hm_list: bool = True
    refresh_calendar: bool = False
    calendar_start: str | None = None
    calendar_end: str | None = None

    @model_validator(mode="after")
    def validate_refresh_options(self) -> RefreshMetadataTaskRequest:
        """校验元数据刷新参数，防止空任务或过大日历范围。"""
        if not any((
            self.refresh_basics,
            self.with_ths,
            self.with_hm_list,
            self.refresh_calendar,
        )):
            raise ValueError("至少需要启用一个刷新项")

        if not self.refresh_calendar:
            return self

        if not self.calendar_start:
            raise ValueError("refresh_calendar=true 时 calendar_start 必填")

        start = _parse_iso_date(self.calendar_start, field_name="calendar_start")
        end = (
            _parse_iso_date(self.calendar_end, field_name="calendar_end")
            if self.calendar_end else _today_cn() + timedelta(days=365)
        )
        if end < start:
            raise ValueError("calendar_end 不能早于 calendar_start")
        if (end - start).days > 365 * 5:
            raise ValueError("交易日历刷新跨度不能超过 5 年")
        return self


class TaskStatusResponse(BaseModel):
    task_id: str | None = None
    task_type: str | None = None
    status: Literal["idle", "running", "success", "error"]
    strategy: str | None = None
    trade_date: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    message: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)


class SchedulerConfig(BaseModel):
    strategy: str = "short"
    skip_ai: bool = False
    cron_hour: int = 15
    cron_minute: int = 30


class SchedulerStatusResponse(BaseModel):
    status: Literal["running", "stopped"]
    strategy: str | None = None
    cron: str | None = None
    next_run: str | None = None


def _parse_iso_date(value: str, *, field_name: str) -> date:
    """解析 YYYY-MM-DD 字符串。

    注意：此版本抛 ValueError 供 Pydantic model_validator 使用。
    tasks.py 有同名函数抛 HTTPException(400) 供 FastAPI 端点使用。
    两处需保持解析逻辑一致但异常类型不同，因此刻意分离。
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 格式错误，需 YYYY-MM-DD") from exc


def _today_cn() -> date:
    """取 A 股交易语义下的今天。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()
