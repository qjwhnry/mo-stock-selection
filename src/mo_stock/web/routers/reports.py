"""报告相关 API。"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mo_stock.storage.models import SelectionResult
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import ReportListItem, ReportListResponse

router = APIRouter(tags=["reports"])


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
