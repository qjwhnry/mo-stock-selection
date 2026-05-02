"""报告相关 API。"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from mo_stock.filters.swing.market_regime_filter import MarketRegimeFilter
from mo_stock.storage.models import (
    AiAnalysis,
    DailyKline,
    FilterScoreDaily,
    IndexMember,
    SelectionResult,
    StockBasic,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import (
    IndexSnapshot,
    MarketData,
    ReportDetailResponse,
    ReportListItem,
    ReportListResponse,
    StockItem,
)

router = APIRouter(tags=["reports"])

VALID_STRATEGIES = {"short", "swing"}

VALID_SORT_BY = {
    "final_score": SelectionResult.final_score,
    "rule_score": SelectionResult.rule_score,
    "ai_score": SelectionResult.ai_score,
    # short dimensions
    "limit": None,
    "moneyflow": None,
    "lhb": None,
    "sector": None,
    "theme": None,
    # swing dimensions
    "trend": None,
    "pullback": None,
    "moneyflow_swing": None,
    "sector_swing": None,
    "theme_swing": None,
    "catalyst": None,
    "risk_liquidity": None,
}


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    db: Annotated[Session, Depends(get_db)],
    strategy: Literal["short", "swing"] = Query(default="short", description="策略标识"),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    order: Literal["asc", "desc"] = Query(default="desc", description="排序方向"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
) -> ReportListResponse:
    """获取报告列表，按交易日期分组返回。

    查询 selection_result 表，按 trade_date 分组统计 count/avg/max，支持分页和排序。
    """
    # 策略白名单校验（FastAPI Literal 已自动校验，但保留防御性检查）
    if strategy not in ("short", "swing"):
        raise HTTPException(status_code=400, detail=f"非法 strategy: {strategy}")

    # 构建基础查询：picked=True 且 strategy 匹配
    base_query = (
        select(SelectionResult)
        .where(SelectionResult.picked.is_(True))
        .where(SelectionResult.strategy == strategy)
    )

    # 总日期数（用于分页）
    base_subq = base_query.subquery()
    count_subq = select(func.count(func.distinct(base_subq.c.trade_date)))
    total = db.execute(count_subq).scalar() or 0

    # 分组查询：按 trade_date 聚合 count/avg/max
    grouped = (
        select(
            SelectionResult.trade_date,
            func.count().label("count"),
            func.avg(SelectionResult.final_score).label("avg_score"),
            func.max(SelectionResult.final_score).label("max_score"),
        )
        .where(SelectionResult.picked.is_(True))
        .where(SelectionResult.strategy == strategy)
        .group_by(SelectionResult.trade_date)
    )

    # 排序
    if order == "desc":
        grouped = grouped.order_by(SelectionResult.trade_date.desc())
    else:
        grouped = grouped.order_by(SelectionResult.trade_date.asc())

    # 分页
    offset = (page - 1) * page_size
    grouped = grouped.offset(offset).limit(page_size)

    # 执行查询
    rows = db.execute(grouped).all()

    # 组装响应
    items = [
        ReportListItem(
            trade_date=str(row.trade_date),
            strategy=strategy,
            count=row.count,
            avg_score=float(row.avg_score),
            max_score=float(row.max_score),
        )
        for row in rows
    ]

    return ReportListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


def _get_market_data(db: Session, trade_date: date) -> MarketData:
    """获取市场数据：上证指数、沪深300、regime_score。"""
    sh_index_row = db.execute(
        select(DailyKline)
        .where(DailyKline.ts_code == "000001.SH")
        .where(DailyKline.trade_date == trade_date)
    ).scalar_one_or_none()

    hs300_index_row = db.execute(
        select(DailyKline)
        .where(DailyKline.ts_code == "000300.SH")
        .where(DailyKline.trade_date == trade_date)
    ).scalar_one_or_none()

    sh_index = IndexSnapshot(
        close=float(sh_index_row.close) if sh_index_row and sh_index_row.close else 0.0,
        pct_chg=float(sh_index_row.pct_chg) if sh_index_row and sh_index_row.pct_chg is not None else 0.0,
    )
    hs300_index = IndexSnapshot(
        close=float(hs300_index_row.close) if hs300_index_row and hs300_index_row.close else 0.0,
        pct_chg=float(hs300_index_row.pct_chg) if hs300_index_row and hs300_index_row.pct_chg is not None else 0.0,
    )

    regime_score = MarketRegimeFilter().score_market(db, trade_date)

    return MarketData(
        sh_index=sh_index,
        hs300_index=hs300_index,
        regime_score=regime_score,
    )


@router.get("/reports/{trade_date}", response_model=ReportDetailResponse)
async def get_report_detail(
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
    strategy: str = Query(default="short", description="策略标识"),
    sort_by: str = Query(default="final_score", description="排序字段"),
    order: str = Query(default="desc", description="排序方向"),
    sector: str | None = Query(default=None, description="行业过滤"),
    keyword: str | None = Query(default=None, description="名称/代码搜索"),
) -> ReportDetailResponse:
    """获取指定日期的报告详情。

    返回当日选股结果、市场数据、可筛选行业列表。
    """
    # 参数校验
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"非法 strategy: {strategy}")
    if sort_by not in VALID_SORT_BY:
        raise HTTPException(status_code=400, detail=f"非法 sort_by: {sort_by}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"非法 order: {order}")

    # 基础查询
    query = (
        select(SelectionResult)
        .where(SelectionResult.trade_date == trade_date)
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.picked.is_(True))
    )

    # 行业过滤
    if sector:
        query = query.join(IndexMember, SelectionResult.ts_code == IndexMember.ts_code)
        query = query.where(IndexMember.l1_name == sector)

    # 关键词搜索
    if keyword:
        query = query.join(StockBasic, SelectionResult.ts_code == StockBasic.ts_code)
        query = query.where(
            (StockBasic.name.contains(keyword)) | (StockBasic.ts_code.contains(keyword))
        )

    # 排序逻辑
    sort_column = VALID_SORT_BY[sort_by]
    if sort_column is not None:
        # 直接按 SelectionResult 列排序
        if order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
    else:
        # 按 filter_score_daily 维度分排序
        dim_join = (
            select(FilterScoreDaily.ts_code, FilterScoreDaily.score)
            .where(FilterScoreDaily.trade_date == trade_date)
            .where(FilterScoreDaily.strategy == strategy)
            .where(FilterScoreDaily.dim == sort_by)
            .subquery()
        )
        query = query.outerjoin(dim_join, SelectionResult.ts_code == dim_join.c.ts_code)
        # coalesce(score, 0) 处理缺失维度，nulls last
        order_expr = case(
            (dim_join.c.score.is_(None), 0 if order == "desc" else 999),
            else_=dim_join.c.score,
        )
        if order == "desc":
            query = query.order_by(order_expr.desc(), SelectionResult.ts_code)
        else:
            query = query.order_by(order_expr.asc(), SelectionResult.ts_code)

    # 执行查询
    results = db.execute(query).scalars().all()
    ts_codes = [r.ts_code for r in results]

    # 批量查询维度分
    scores_map: dict[str, dict[str, int]] = {}
    if ts_codes:
        score_rows = db.execute(
            select(FilterScoreDaily)
            .where(FilterScoreDaily.trade_date == trade_date)
            .where(FilterScoreDaily.strategy == strategy)
            .where(FilterScoreDaily.ts_code.in_(ts_codes))
        ).scalars().all()
        for row in score_rows:
            if row.ts_code not in scores_map:
                scores_map[row.ts_code] = {}
            scores_map[row.ts_code][row.dim] = int(row.score)

    # 批量查询 AI 分析
    thesis_map: dict[str, str] = {}
    if ts_codes:
        ai_rows = db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.trade_date == trade_date)
            .where(AiAnalysis.strategy == strategy)
            .where(AiAnalysis.ts_code.in_(ts_codes))
        ).scalars().all()
        for row in ai_rows:
            if row.thesis:
                thesis = row.thesis
                if len(thesis) > 100:
                    thesis = thesis[:100] + "..."
                thesis_map[row.ts_code] = thesis

    # 批量查询行业
    industry_map: dict[str, str] = {}
    if ts_codes:
        idx_rows = db.execute(
            select(IndexMember)
            .where(IndexMember.ts_code.in_(ts_codes))
        ).scalars().all()
        for row in idx_rows:
            if row.l1_name:
                industry_map[row.ts_code] = row.l1_name

    # 批量查询股票名称
    name_map: dict[str, str] = {}
    if ts_codes:
        stock_rows = db.execute(
            select(StockBasic)
            .where(StockBasic.ts_code.in_(ts_codes))
        ).scalars().all()
        for row in stock_rows:
            name_map[row.ts_code] = row.name

    # 组装 StockItem 列表
    stocks = []
    for r in results:
        stocks.append(
            StockItem(
                rank=r.rank,
                ts_code=r.ts_code,
                name=name_map.get(r.ts_code, ""),
                industry=industry_map.get(r.ts_code, ""),
                final_score=round(float(r.final_score), 1),
                rule_score=round(float(r.rule_score), 1),
                ai_score=round(float(r.ai_score), 1) if r.ai_score is not None else None,
                scores=scores_map.get(r.ts_code, {}),
                ai_summary=thesis_map.get(r.ts_code),
                picked=r.picked,
            )
        )

    # 获取可用行业列表（从本次 selection 中去重）
    available_sectors = sorted(set(industry_map.values()))

    # 获取市场数据
    market = _get_market_data(db, trade_date)

    return ReportDetailResponse(
        trade_date=str(trade_date),
        strategy=strategy,
        market=market,
        stocks=stocks,
        available_sectors=available_sectors,
    )
