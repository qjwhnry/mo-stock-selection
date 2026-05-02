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
