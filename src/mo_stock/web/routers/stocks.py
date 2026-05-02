"""个股相关 API。"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from mo_stock.storage.models import (
    AiAnalysis,
    FilterScoreDaily,
    IndexMember,
    SelectionResult,
    StockBasic,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import AiAnalysisData, RecentPick, StockDetailResponse

router = APIRouter(tags=["stocks"])


@router.get("/stocks/{ts_code}", response_model=StockDetailResponse)
def get_stock_detail(
    ts_code: str,
    db: Annotated[Session, Depends(get_db)],
    strategy: Literal["short", "swing"] = Query(default="short", description="策略类型: short 或 swing"),
    days: int = Query(default=10, ge=1, le=100, description="查询最近 N 天的选股记录"),
) -> StockDetailResponse:
    """获取单股详情：基础信息、最新维度分、AI 分析、最近选股记录。

    Args:
        ts_code: 股票代码，如 600519.SH
        strategy: 策略类型，short（默认）或 swing
        days: 返回最近几天的选股记录，默认 10，范围 1-100

    Returns:
        StockDetailResponse: 包含基础信息、最新维度分、AI 分析、最近选股记录

    Raises:
        404: 股票代码不存在
    """
    # 1. 查询股票基础信息
    stock = db.query(StockBasic).filter(StockBasic.ts_code == ts_code).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {ts_code} not found")

    # 3. 获取行业信息（优先从 IndexMember，fallback 到 stock_basic.industry）
    index_member = db.query(IndexMember).filter(IndexMember.ts_code == ts_code).first()
    industry = index_member.l1_name if index_member and index_member.l1_name else (stock.industry or "未知")

    # 4. 获取最新维度分
    # 找最近有评分记录的交易日
    latest_score_record = (
        db.query(FilterScoreDaily.trade_date)
        .filter(
            FilterScoreDaily.ts_code == ts_code,
            FilterScoreDaily.strategy == strategy,
        )
        .order_by(FilterScoreDaily.trade_date.desc())
        .first()
    )

    latest_scores: dict[str, int] = {}
    if latest_score_record:
        latest_date = latest_score_record.trade_date
        score_rows = (
            db.query(FilterScoreDaily)
            .filter(
                FilterScoreDaily.ts_code == ts_code,
                FilterScoreDaily.strategy == strategy,
                FilterScoreDaily.trade_date == latest_date,
            )
            .all()
        )
        latest_scores = {row.dim: int(row.score) for row in score_rows}

    # 5. 获取最新 AI 分析
    ai_analysis_row = (
        db.query(AiAnalysis)
        .filter(
            AiAnalysis.ts_code == ts_code,
            AiAnalysis.strategy == strategy,
        )
        .order_by(AiAnalysis.trade_date.desc())
        .first()
    )

    ai_analysis: AiAnalysisData | None = None
    if ai_analysis_row:
        ai_analysis = AiAnalysisData(
            thesis=ai_analysis_row.thesis,
            key_catalysts=ai_analysis_row.key_catalysts,
            risks=ai_analysis_row.risks,
            suggested_entry=ai_analysis_row.suggested_entry,
            stop_loss=ai_analysis_row.stop_loss,
        )

    # 6. 获取最近选股记录
    selection_rows = (
        db.query(SelectionResult)
        .filter(
            SelectionResult.ts_code == ts_code,
            SelectionResult.strategy == strategy,
        )
        .order_by(SelectionResult.trade_date.desc())
        .limit(days)
        .all()
    )

    recent_picks = [
        RecentPick(
            trade_date=row.trade_date.isoformat(),
            picked=bool(row.picked),
            final_score=round(float(row.final_score), 1),
        )
        for row in selection_rows
    ]

    return StockDetailResponse(
        ts_code=stock.ts_code,
        name=stock.name,
        industry=industry,
        latest_scores=latest_scores,
        ai_analysis=ai_analysis,
        recent_picks=recent_picks,
    )
