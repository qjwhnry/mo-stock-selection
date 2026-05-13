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
    LimitConceptDaily,
    SelectionResult,
    StockBasic,
    ThsIndex,
    ThsMember,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import (
    DimensionTopResponse,
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
    "limit_restart": None,
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

REPORT_CONCEPT_DISPLAY_LIMIT = 5


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
    items: list[ReportListItem] = []
    for row in rows:
        row_map = row._mapping
        items.append(
            ReportListItem(
                trade_date=str(row_map["trade_date"]),
                strategy=strategy,
                count=int(row_map["count"]),
                avg_score=float(row_map["avg_score"] or 0),
                max_score=float(row_map["max_score"] or 0),
            )
        )

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
        # 直接按 SelectionResult 列排序，nullable 列使用 nulls_last
        if order == "desc":
            query = query.order_by(sort_column.desc().nulls_last())
        else:
            query = query.order_by(sort_column.asc().nulls_last())
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

    # 批量查询维度分（含 detail）
    scores_map: dict[str, dict[str, int]] = {}
    details_map: dict[str, dict[str, dict]] = {}
    if ts_codes:
        score_rows = db.execute(
            select(FilterScoreDaily)
            .where(FilterScoreDaily.trade_date == trade_date)
            .where(FilterScoreDaily.strategy == strategy)
            .where(FilterScoreDaily.ts_code.in_(ts_codes))
        ).scalars().all()
        for score_row in score_rows:
            if score_row.ts_code not in scores_map:
                scores_map[score_row.ts_code] = {}
                details_map[score_row.ts_code] = {}
            scores_map[score_row.ts_code][score_row.dim] = int(score_row.score)
            details_map[score_row.ts_code][score_row.dim] = score_row.detail or {}

    # 批量查询 AI 分析
    thesis_map: dict[str, str] = {}
    if ts_codes:
        ai_rows = db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.trade_date == trade_date)
            .where(AiAnalysis.strategy == strategy)
            .where(AiAnalysis.ts_code.in_(ts_codes))
        ).scalars().all()
        for ai_row in ai_rows:
            if ai_row.thesis:
                thesis = ai_row.thesis
                if len(thesis) > 100:
                    thesis = thesis[:100] + "..."
                thesis_map[ai_row.ts_code] = thesis

    # 批量查询行业
    industry_map: dict[str, str] = {}
    if ts_codes:
        idx_rows = db.execute(
            select(IndexMember)
            .where(IndexMember.ts_code.in_(ts_codes))
        ).scalars().all()
        for idx_row in idx_rows:
            if idx_row.l1_name:
                industry_map[idx_row.ts_code] = idx_row.l1_name

    # 批量查询股票名称
    name_map: dict[str, str] = {}
    if ts_codes:
        stock_rows = db.execute(
            select(StockBasic)
            .where(StockBasic.ts_code.in_(ts_codes))
        ).scalars().all()
        for stock_row in stock_rows:
            name_map[stock_row.ts_code] = stock_row.name

    # 批量查询概念/题材，按当日概念强度排序；接口只返回列表页展示所需数量。
    concepts_map: dict[str, list[str]] = {c: [] for c in ts_codes}
    concept_count_map: dict[str, int] = dict.fromkeys(ts_codes, 0)
    if ts_codes:
        concept_rows = db.execute(
            select(ThsMember.con_code, ThsIndex.name)
            .join(ThsIndex, ThsMember.ts_code == ThsIndex.ts_code)
            .outerjoin(
                LimitConceptDaily,
                (ThsMember.ts_code == LimitConceptDaily.ts_code)
                & (LimitConceptDaily.trade_date == trade_date),
            )
            .where(ThsMember.con_code.in_(ts_codes))
            .where(ThsIndex.name.isnot(None))
            .order_by(
                LimitConceptDaily.rank.is_(None),
                LimitConceptDaily.rank,
                ThsIndex.name,
            )
        ).all()
        for con_code, concept_name in concept_rows:
            concept_count_map[con_code] = concept_count_map.get(con_code, 0) + 1
            current = concepts_map.setdefault(con_code, [])
            if len(current) < REPORT_CONCEPT_DISPLAY_LIMIT:
                current.append(concept_name)

    # 组装 StockItem 列表
    stocks = []
    for r in results:
        stocks.append(
            StockItem(
                rank=r.rank,
                ts_code=r.ts_code,
                name=name_map.get(r.ts_code, ""),
                industry=industry_map.get(r.ts_code, ""),
                concepts=concepts_map.get(r.ts_code, []),
                concept_count=concept_count_map.get(r.ts_code, 0),
                final_score=round(float(r.final_score), 1),
                rule_score=round(float(r.rule_score), 1),
                ai_score=round(float(r.ai_score), 1) if r.ai_score is not None else None,
                scores=scores_map.get(r.ts_code, {}),
                score_details=details_map.get(r.ts_code, {}),
                ai_summary=thesis_map.get(r.ts_code),
                picked=r.picked,
            )
        )

    # 获取可用行业列表（从当日全部入选股，与当前筛选结果解耦）
    all_picked_codes = db.execute(
        select(SelectionResult.ts_code)
        .where(SelectionResult.trade_date == trade_date)
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.picked.is_(True))
    ).scalars().all()
    all_sector_rows = db.execute(
        select(IndexMember.ts_code, IndexMember.l1_name)
        .where(IndexMember.ts_code.in_(all_picked_codes))
    ).all() if all_picked_codes else []
    available_sectors = sorted({r.l1_name for r in all_sector_rows if r.l1_name})

    # 获取市场数据
    market = _get_market_data(db, trade_date)

    return ReportDetailResponse(
        trade_date=str(trade_date),
        strategy=strategy,
        market=market,
        stocks=stocks,
        available_sectors=available_sectors,
    )


_SHORT_DIMS = {"limit", "limit_restart", "moneyflow", "lhb", "sector", "theme", "exhaustion"}
_SWING_DIMS = {"trend", "pullback", "moneyflow_swing", "sector_swing", "theme_swing", "catalyst", "risk_liquidity"}


@router.get("/reports/{trade_date}/dimension-top", response_model=DimensionTopResponse)
async def get_dimension_top(
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
    dim: str = Query(..., description="维度名称，如 lhb / limit / moneyflow"),
    strategy: str = Query(default="short", description="策略标识"),
    limit: int = Query(default=20, ge=5, le=50, description="返回数量"),
) -> DimensionTopResponse:
    """获取指定维度的 Top N 股票（独立于综合选股结果）。

    从全候选池按该维度得分降序取前 N 只，过滤 ST 和停牌；
    返回结构与 StockItem 一致，picked 标记是否在当日综合入选名单中。
    """
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"非法 strategy: {strategy}")
    valid_dims = _SHORT_DIMS if strategy == "short" else _SWING_DIMS
    if dim not in valid_dims:
        raise HTTPException(status_code=400, detail=f"非法 dim: {dim}，{strategy} 策略有效维度: {sorted(valid_dims)}")

    # 主查询：按维度分降序取 Top N，过滤 ST 和停牌
    base_query = (
        select(
            FilterScoreDaily.ts_code,
            FilterScoreDaily.score,
            StockBasic.name,
        )
        .join(StockBasic, FilterScoreDaily.ts_code == StockBasic.ts_code)
        .join(DailyKline, (FilterScoreDaily.ts_code == DailyKline.ts_code) & (DailyKline.trade_date == trade_date))
        .where(FilterScoreDaily.trade_date == trade_date)
        .where(FilterScoreDaily.strategy == strategy)
        .where(FilterScoreDaily.dim == dim)
        .where(FilterScoreDaily.score > 0)
        .where(DailyKline.vol > 0)  # 排除停牌：必须有当日行情且成交量 > 0
        .where(StockBasic.is_st.is_(False))
        .where(~func.upper(func.trim(StockBasic.name)).like("ST%"))
        .where(~func.upper(func.trim(StockBasic.name)).like("*ST%"))
        .order_by(FilterScoreDaily.score.desc(), FilterScoreDaily.ts_code.asc())  # 同分按代码稳定排序
        .limit(limit)
    )
    rows = db.execute(base_query).all()
    ts_codes = [r.ts_code for r in rows]
    name_from_main = {r.ts_code: r.name for r in rows}

    if not ts_codes:
        return DimensionTopResponse(trade_date=str(trade_date), strategy=strategy, dim=dim, stocks=[])

    # 批量查询所有维度分
    scores_map: dict[str, dict[str, int]] = {}
    details_map: dict[str, dict[str, dict]] = {}
    score_rows = db.execute(
        select(FilterScoreDaily)
        .where(FilterScoreDaily.trade_date == trade_date)
        .where(FilterScoreDaily.strategy == strategy)
        .where(FilterScoreDaily.ts_code.in_(ts_codes))
    ).scalars().all()
    for sr in score_rows:
        scores_map.setdefault(sr.ts_code, {})[sr.dim] = int(sr.score)
        details_map.setdefault(sr.ts_code, {})[sr.dim] = sr.detail or {}

    # 批量查询 selection_result（获取 final_score / rule_score / ai_score / picked / rank）
    sel_map: dict[str, dict] = {}
    sel_rows = db.execute(
        select(SelectionResult)
        .where(SelectionResult.trade_date == trade_date)
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.ts_code.in_(ts_codes))
    ).scalars().all()
    for sel_row in sel_rows:
        sel_map[sel_row.ts_code] = {
            "final_score": float(sel_row.final_score),
            "rule_score": float(sel_row.rule_score),
            "ai_score": float(sel_row.ai_score) if sel_row.ai_score is not None else None,
            "picked": sel_row.picked,
            "rank": sel_row.rank if sel_row.picked else 0,
        }

    # 批量查询 AI 分析
    thesis_map: dict[str, str] = {}
    ai_rows = db.execute(
        select(AiAnalysis)
        .where(AiAnalysis.trade_date == trade_date)
        .where(AiAnalysis.strategy == strategy)
        .where(AiAnalysis.ts_code.in_(ts_codes))
    ).scalars().all()
    for ai_row in ai_rows:
        if ai_row.thesis:
            thesis = ai_row.thesis
            if len(thesis) > 100:
                thesis = thesis[:100] + "..."
            thesis_map[ai_row.ts_code] = thesis

    # 批量查询行业
    industry_map: dict[str, str] = {}
    idx_rows = db.execute(
        select(IndexMember)
        .where(IndexMember.ts_code.in_(ts_codes))
    ).scalars().all()
    for idx_row in idx_rows:
        if idx_row.l1_name:
            industry_map[idx_row.ts_code] = idx_row.l1_name

    # 批量查询概念（与报告详情一致，按当日概念强度排序）
    concepts_map: dict[str, list[str]] = {c: [] for c in ts_codes}
    concept_count_map: dict[str, int] = dict.fromkeys(ts_codes, 0)
    concept_rows = db.execute(
        select(ThsMember.con_code, ThsIndex.name)
        .join(ThsIndex, ThsMember.ts_code == ThsIndex.ts_code)
        .outerjoin(
            LimitConceptDaily,
            (ThsMember.ts_code == LimitConceptDaily.ts_code)
            & (LimitConceptDaily.trade_date == trade_date),
        )
        .where(ThsMember.con_code.in_(ts_codes))
        .where(ThsIndex.name.isnot(None))
        .order_by(
            LimitConceptDaily.rank.is_(None),
            LimitConceptDaily.rank,
            ThsIndex.name,
        )
    ).all()
    for con_code, concept_name in concept_rows:
        concept_count_map[con_code] = concept_count_map.get(con_code, 0) + 1
        current = concepts_map.setdefault(con_code, [])
        if len(current) < REPORT_CONCEPT_DISPLAY_LIMIT:
            current.append(concept_name)

    # 组装 StockItem 列表，按维度分降序排列，同分按代码稳定排序
    stocks: list[StockItem] = []
    for rank_idx, ts_code in enumerate(ts_codes, start=1):
        sel = sel_map.get(ts_code, {})
        stocks.append(
            StockItem(
                rank=rank_idx,
                ts_code=ts_code,
                name=name_from_main.get(ts_code, ""),
                industry=industry_map.get(ts_code, ""),
                concepts=concepts_map.get(ts_code, []),
                concept_count=concept_count_map.get(ts_code, 0),
                final_score=sel.get("final_score", 0.0),
                rule_score=sel.get("rule_score", 0.0),
                ai_score=sel.get("ai_score"),
                scores=scores_map.get(ts_code, {}),
                score_details=details_map.get(ts_code, {}),
                ai_summary=thesis_map.get(ts_code),
                picked=sel.get("picked", False),
            )
        )

    return DimensionTopResponse(
        trade_date=str(trade_date),
        strategy=strategy,
        dim=dim,
        stocks=stocks,
    )
