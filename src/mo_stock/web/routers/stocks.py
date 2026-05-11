"""个股相关 API。"""
from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.backtest.utils import trade_dates_between
from mo_stock.storage.models import (
    AiAnalysis,
    DailyKline,
    FilterScoreDaily,
    IndexMember,
    LimitConceptDaily,
    SelectionResult,
    StockBasic,
    ThsIndex,
    ThsMember,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import AiAnalysisData, RecentPick, StockDetailResponse

router = APIRouter(tags=["stocks"])

STOCK_DETAIL_CONCEPT_DISPLAY_LIMIT = 15


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

    # 4.5 获取概念/题材列表，按当日概念强度排序
    if target_date:
        concept_rows = db.execute(
            select(ThsIndex.name)
            .join(ThsMember, ThsMember.ts_code == ThsIndex.ts_code)
            .outerjoin(
                LimitConceptDaily,
                (ThsMember.ts_code == LimitConceptDaily.ts_code)
                & (LimitConceptDaily.trade_date == target_date),
            )
            .where(ThsMember.con_code == ts_code)
            .where(ThsIndex.name.isnot(None))
            .order_by(
                LimitConceptDaily.rank.is_(None),
                LimitConceptDaily.rank,
                ThsIndex.name,
            )
        ).scalars().all()
    else:
        concept_rows = db.execute(
            select(ThsIndex.name)
            .join(ThsMember, ThsMember.ts_code == ThsIndex.ts_code)
            .where(ThsMember.con_code == ts_code)
            .where(ThsIndex.name.isnot(None))
            .order_by(ThsIndex.name)
        ).scalars().all()
    all_concepts = [name for name in concept_rows if name]
    concepts = all_concepts[:STOCK_DETAIL_CONCEPT_DISPLAY_LIMIT]

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

    recent_picks: list[RecentPick] = []

    # 批量计算前向收益（需交易日历 + K 线数据）
    fwd_enabled = bool(selection_rows)
    if fwd_enabled:
        try:
            from datetime import timedelta

            min_date = min(row.trade_date for row in selection_rows)
            max_date = max(row.trade_date for row in selection_rows)
            all_td = trade_dates_between(db, min_date, max_date + timedelta(days=20))
        except Exception:
            all_td = []
            fwd_enabled = False

    if fwd_enabled and all_td:
        td_sorted = sorted(all_td)
        needed = [d for d in td_sorted if d >= min_date]
        klines = (
            db.query(DailyKline)
            .filter(
                DailyKline.ts_code == ts_code,
                DailyKline.trade_date.in_(needed),
            )
            .all()
        )
        close_map: dict[date_type, float] = {
            k.trade_date: k.close for k in klines if k.close is not None
        }

        for row in selection_rows:
            fwd_5d: float | None = None
            fwd_10d: float | None = None
            entry_close = close_map.get(row.trade_date)
            if entry_close and entry_close > 0:
                try:
                    idx = td_sorted.index(row.trade_date)
                except ValueError:
                    idx = -1
                if idx >= 0:
                    future_dates = td_sorted[idx + 1 : idx + 11]
                    if len(future_dates) >= 5:
                        exit_5 = close_map.get(future_dates[4])
                        if exit_5 and exit_5 > 0:
                            fwd_5d = round((exit_5 - entry_close) / entry_close * 100, 2)
                    if len(future_dates) >= 10:
                        exit_10 = close_map.get(future_dates[9])
                        if exit_10 and exit_10 > 0:
                            fwd_10d = round((exit_10 - entry_close) / entry_close * 100, 2)

            recent_picks.append(
                RecentPick(
                    trade_date=row.trade_date.isoformat(),
                    picked=bool(row.picked),
                    final_score=round(float(row.final_score), 1),
                    forward_return_5d=fwd_5d,
                    forward_return_10d=fwd_10d,
                )
            )
    else:
        for row in selection_rows:
            recent_picks.append(
                RecentPick(
                    trade_date=row.trade_date.isoformat(),
                    picked=bool(row.picked),
                    final_score=round(float(row.final_score), 1),
                )
            )

    return StockDetailResponse(
        ts_code=stock.ts_code,
        name=stock.name,
        industry=industry,
        concepts=concepts,
        concept_count=len(all_concepts),
        latest_scores=latest_scores,
        score_details=latest_details,
        ai_score=ai_score,
        ai_analysis=ai_analysis,
        recent_picks=recent_picks,
    )
