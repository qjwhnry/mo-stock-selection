"""个股相关 API。"""
from __future__ import annotations

from datetime import date as date_type
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
    strategy: Annotated[
        Literal["short", "swing"],
        Query(description="策略类型: short 或 swing"),
    ] = "short",
    trade_date: Annotated[
        date_type | None,
        Query(description="指定日期查询；不传则取最新"),
    ] = None,
    days: Annotated[
        int,
        Query(ge=1, le=100, description="查询最近 N 天的选股记录"),
    ] = 10,
) -> StockDetailResponse:
    """获取单股详情：基础信息、维度分、AI 分析、最近选股记录。

    Args:
        ts_code: 股票代码，如 600519.SH
        strategy: 策略类型，short（默认）或 swing
        trade_date: 指定日期查询维度分和 AI 分析；不传则自动取最新
        days: 返回最近几天的选股记录，默认 10，范围 1-100

    Returns:
        StockDetailResponse: 包含基础信息、维度分、AI 分析、最近选股记录

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

    # 4. 获取维度分（含 detail）
    # 如果指定了 trade_date 则直接用，否则取最新有评分的日期
    target_date: date_type | None
    if trade_date:
        target_date = trade_date
    else:
        latest_score_record = (
            db.query(FilterScoreDaily.trade_date)
            .filter(
                FilterScoreDaily.ts_code == ts_code,
                FilterScoreDaily.strategy == strategy,
            )
            .order_by(FilterScoreDaily.trade_date.desc())
            .first()
        )
        target_date = latest_score_record.trade_date if latest_score_record else None

    latest_scores: dict[str, int] = {}
    latest_details: dict[str, dict] = {}
    if target_date:
        score_rows = (
            db.query(FilterScoreDaily)
            .filter(
                FilterScoreDaily.ts_code == ts_code,
                FilterScoreDaily.strategy == strategy,
                FilterScoreDaily.trade_date == target_date,
            )
            .all()
        )
        latest_scores = {row.dim: int(row.score) for row in score_rows}
        latest_details = {row.dim: row.detail or {} for row in score_rows}

    # 5. 获取 AI 分析：优先取 target_date 当天，没有则 fallback 到最新
    ai_analysis_row = None
    if target_date:
        ai_analysis_row = (
            db.query(AiAnalysis)
            .filter(
                AiAnalysis.ts_code == ts_code,
                AiAnalysis.strategy == strategy,
                AiAnalysis.trade_date == target_date,
            )
            .first()
        )
    if not ai_analysis_row:
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
    ai_score: float | None = None
    if ai_analysis_row:
        ai_analysis = AiAnalysisData(
            thesis=ai_analysis_row.thesis,
            key_catalysts=ai_analysis_row.key_catalysts,
            risks=ai_analysis_row.risks,
            suggested_entry=ai_analysis_row.suggested_entry,
            stop_loss=ai_analysis_row.stop_loss,
        )
        ai_score = round(float(ai_analysis_row.ai_score), 1) if ai_analysis_row.ai_score is not None else None

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
        score_details=latest_details,
        ai_score=ai_score,
        ai_analysis=ai_analysis,
        recent_picks=recent_picks,
    )
