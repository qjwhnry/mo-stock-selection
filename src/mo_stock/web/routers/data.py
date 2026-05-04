"""数据库数据洞察 API。"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session

from mo_stock.storage.models import (
    DailyKline,
    FilterScoreDaily,
    IndexMember,
    Lhb,
    LhbSeatDetail,
    Moneyflow,
    SelectionResult,
    StockBasic,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import (
    LhbSeatItem,
    LhbSeatsResponse,
    LhbSummaryItem,
    LhbSummaryResponse,
    LhbSummaryStats,
    MoneyflowSummaryItem,
    MoneyflowSummaryResponse,
    MoneyflowSummaryStats,
    SectorListResponse,
    StockKlineSignal,
    StockLhbSignal,
    StockMoneyflowSignal,
    StockScoreSignal,
    StockSelectionSignal,
    StockSignalsResponse,
)

router = APIRouter(tags=["data"])

MONEYFLOW_SCORE_DIMS = ("moneyflow", "moneyflow_swing")
LHB_SCORE_DIMS = ("lhb", "catalyst")


def _industry_expr():
    return func.coalesce(IndexMember.l1_name, StockBasic.sw_l1, StockBasic.industry)


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _wan_or_none(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value) / 10000, digits)


def _moneyflow_ratio(net_mf_wan: Any, amount_qian: Any) -> float | None:
    if net_mf_wan is None or amount_qian in (None, 0):
        return None
    return round(1000 * float(net_mf_wan) / float(amount_qian), 2)


def _moneyflow_ratio_expr():
    return case(
        (
            and_(Moneyflow.net_mf_amount.is_not(None), DailyKline.amount.is_not(None), DailyKline.amount != 0),
            1000 * Moneyflow.net_mf_amount / DailyKline.amount,
        ),
        else_=None,
    )


def _apply_common_filters(query, keyword: str | None, sector: str | None):
    industry = _industry_expr()
    if keyword:
        query = query.where(
            or_(
                StockBasic.name.contains(keyword),
                StockBasic.symbol.contains(keyword),
                StockBasic.ts_code.contains(keyword),
            )
        )
    if sector:
        query = query.where(industry == sector)
    return query


def _apply_order(query: Any, sort_exprs: Mapping[str, Any], sort_by: str, order: str):
    if sort_by not in sort_exprs:
        raise HTTPException(status_code=400, detail=f"非法 sort_by: {sort_by}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"非法 order: {order}")

    sort_expr = sort_exprs[sort_by]
    if order == "asc":
        return query.order_by(sort_expr.asc().nulls_last())
    return query.order_by(sort_expr.desc().nulls_last())


def _score_map(
    db: Session,
    trade_date: date,
    strategy: str,
    ts_codes: list[str],
    dims: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    if not ts_codes:
        return {}

    rows = db.execute(
        select(FilterScoreDaily.ts_code, FilterScoreDaily.dim, FilterScoreDaily.score)
        .where(FilterScoreDaily.trade_date == trade_date)
        .where(FilterScoreDaily.strategy == strategy)
        .where(FilterScoreDaily.ts_code.in_(ts_codes))
        .where(FilterScoreDaily.dim.in_(dims))
    ).all()

    scores: dict[str, dict[str, int]] = {}
    for ts_code, dim, score in rows:
        scores.setdefault(ts_code, {})[dim] = int(score)
    return scores


def _seat_summary_map(db: Session, trade_date: date, ts_codes: list[str]) -> dict[str, dict[str, int]]:
    if not ts_codes:
        return {}

    rows = db.execute(
        select(LhbSeatDetail.ts_code, LhbSeatDetail.seat_type, func.count().label("count"))
        .where(LhbSeatDetail.trade_date == trade_date)
        .where(LhbSeatDetail.ts_code.in_(ts_codes))
        .group_by(LhbSeatDetail.ts_code, LhbSeatDetail.seat_type)
    ).all()

    result: dict[str, dict[str, int]] = {}
    for ts_code, seat_type, count in rows:
        result.setdefault(ts_code, {})[seat_type] = int(count)
    return result


def _base_moneyflow_query(trade_date: date, strategy: str):
    industry = _industry_expr().label("industry")
    ratio = _moneyflow_ratio_expr().label("net_mf_ratio_pct")
    return (
        select(
            Moneyflow.ts_code,
            StockBasic.name,
            industry,
            DailyKline.close,
            DailyKline.pct_chg,
            DailyKline.amount.label("kline_amount"),
            Moneyflow.net_mf_amount,
            Moneyflow.buy_lg_amount,
            Moneyflow.sell_lg_amount,
            Moneyflow.buy_elg_amount,
            Moneyflow.sell_elg_amount,
            ratio,
            SelectionResult.picked,
            SelectionResult.rule_score,
            SelectionResult.final_score,
        )
        .select_from(Moneyflow)
        .outerjoin(
            DailyKline,
            and_(
                DailyKline.ts_code == Moneyflow.ts_code,
                DailyKline.trade_date == Moneyflow.trade_date,
            ),
        )
        .outerjoin(StockBasic, StockBasic.ts_code == Moneyflow.ts_code)
        .outerjoin(IndexMember, IndexMember.ts_code == Moneyflow.ts_code)
        .outerjoin(
            SelectionResult,
            and_(
                SelectionResult.ts_code == Moneyflow.ts_code,
                SelectionResult.trade_date == Moneyflow.trade_date,
                SelectionResult.strategy == strategy,
            ),
        )
        .where(Moneyflow.trade_date == trade_date)
    )


@router.get("/data/moneyflow-summary", response_model=MoneyflowSummaryResponse)
def get_moneyflow_summary(
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
    strategy: Literal["short", "swing"] = Query(default="short"),
    keyword: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    sort_by: str = Query(default="net_mf_ratio_pct"),
    order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> MoneyflowSummaryResponse:
    """按交易日分页查看主力资金流。"""
    base = _base_moneyflow_query(trade_date, strategy)
    base = _apply_common_filters(base, keyword, sector)

    sort_exprs = {
        "net_mf_ratio_pct": _moneyflow_ratio_expr(),
        "net_mf_wan": Moneyflow.net_mf_amount,
        "pct_chg": DailyKline.pct_chg,
        "final_score": SelectionResult.final_score,
        "rule_score": SelectionResult.rule_score,
    }
    ordered = _apply_order(base, sort_exprs, sort_by, order)

    filtered = base.subquery()
    total = db.execute(select(func.count()).select_from(filtered)).scalar() or 0
    summary_row = db.execute(
        select(
            func.count(case((filtered.c.net_mf_amount > 0, 1))).label("positive_count"),
            func.sum(filtered.c.net_mf_amount).label("total_net_mf_wan"),
        )
    ).one()

    rows = db.execute(ordered.offset((page - 1) * page_size).limit(page_size)).all()
    ts_codes = [row.ts_code for row in rows]
    scores = _score_map(db, trade_date, strategy, ts_codes, MONEYFLOW_SCORE_DIMS)

    return MoneyflowSummaryResponse(
        items=[
            MoneyflowSummaryItem(
                ts_code=row.ts_code,
                name=row.name or "",
                industry=row.industry,
                close=_round_or_none(row.close),
                pct_chg=_round_or_none(row.pct_chg),
                net_mf_wan=_round_or_none(row.net_mf_amount),
                net_mf_ratio_pct=_round_or_none(row.net_mf_ratio_pct),
                buy_lg_wan=_round_or_none(row.buy_lg_amount),
                sell_lg_wan=_round_or_none(row.sell_lg_amount),
                buy_elg_wan=_round_or_none(row.buy_elg_amount),
                sell_elg_wan=_round_or_none(row.sell_elg_amount),
                picked=bool(row.picked) if row.picked is not None else False,
                rule_score=_round_or_none(row.rule_score, 1),
                final_score=_round_or_none(row.final_score, 1),
                scores=scores.get(row.ts_code, {}),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        summary=MoneyflowSummaryStats(
            net_mf_positive_count=int(summary_row.positive_count or 0),
            total_net_mf_wan=_round_or_none(summary_row.total_net_mf_wan),
        ),
    )


def _base_lhb_query(trade_date: date, strategy: str):
    industry = _industry_expr().label("industry")
    return (
        select(
            Lhb.ts_code,
            StockBasic.name,
            industry,
            DailyKline.close,
            DailyKline.pct_chg,
            Lhb.l_buy,
            Lhb.l_sell,
            Lhb.l_amount,
            Lhb.net_amount,
            Lhb.net_rate,
            Lhb.amount_rate,
            Lhb.reason,
            SelectionResult.picked,
            SelectionResult.rule_score,
            SelectionResult.final_score,
        )
        .select_from(Lhb)
        .outerjoin(
            DailyKline,
            and_(DailyKline.ts_code == Lhb.ts_code, DailyKline.trade_date == Lhb.trade_date),
        )
        .outerjoin(StockBasic, StockBasic.ts_code == Lhb.ts_code)
        .outerjoin(IndexMember, IndexMember.ts_code == Lhb.ts_code)
        .outerjoin(
            SelectionResult,
            and_(
                SelectionResult.ts_code == Lhb.ts_code,
                SelectionResult.trade_date == Lhb.trade_date,
                SelectionResult.strategy == strategy,
            ),
        )
        .where(Lhb.trade_date == trade_date)
    )


@router.get("/data/lhb-summary", response_model=LhbSummaryResponse)
def get_lhb_summary(
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
    strategy: Literal["short", "swing"] = Query(default="short"),
    keyword: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    sort_by: str = Query(default="lhb_net_rate_pct"),
    order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> LhbSummaryResponse:
    """按交易日分页查看龙虎榜汇总。"""
    base = _base_lhb_query(trade_date, strategy)
    base = _apply_common_filters(base, keyword, sector)

    sort_exprs = {
        "lhb_net_rate_pct": Lhb.net_rate,
        "lhb_net_amount_wan": Lhb.net_amount,
        "pct_chg": DailyKline.pct_chg,
        "final_score": SelectionResult.final_score,
        "rule_score": SelectionResult.rule_score,
    }
    ordered = _apply_order(base, sort_exprs, sort_by, order)

    filtered = base.subquery()
    total = db.execute(select(func.count()).select_from(filtered)).scalar() or 0
    summary_row = db.execute(
        select(
            func.sum(filtered.c.net_amount).label("total_lhb_net_amount"),
        )
    ).one()
    filtered_codes = select(filtered.c.ts_code)
    institution_net_buy_count = db.execute(
        select(func.count(distinct(LhbSeatDetail.ts_code)))
        .where(LhbSeatDetail.trade_date == trade_date)
        .where(LhbSeatDetail.seat_type == "institution")
        .where(LhbSeatDetail.net_buy > 0)
        .where(LhbSeatDetail.ts_code.in_(filtered_codes))
    ).scalar() or 0

    rows = db.execute(ordered.offset((page - 1) * page_size).limit(page_size)).all()
    ts_codes = [row.ts_code for row in rows]
    scores = _score_map(db, trade_date, strategy, ts_codes, LHB_SCORE_DIMS)
    seat_summary = _seat_summary_map(db, trade_date, ts_codes)

    return LhbSummaryResponse(
        items=[
            LhbSummaryItem(
                ts_code=row.ts_code,
                name=row.name or "",
                industry=row.industry,
                close=_round_or_none(row.close),
                pct_chg=_round_or_none(row.pct_chg),
                lhb_buy_wan=_wan_or_none(row.l_buy),
                lhb_sell_wan=_wan_or_none(row.l_sell),
                lhb_amount_wan=_wan_or_none(row.l_amount),
                lhb_net_amount_wan=_wan_or_none(row.net_amount),
                lhb_net_rate_pct=_round_or_none(row.net_rate),
                lhb_amount_rate_pct=_round_or_none(row.amount_rate),
                reason=row.reason,
                seat_summary=seat_summary.get(row.ts_code, {}),
                picked=bool(row.picked) if row.picked is not None else False,
                rule_score=_round_or_none(row.rule_score, 1),
                final_score=_round_or_none(row.final_score, 1),
                scores=scores.get(row.ts_code, {}),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        summary=LhbSummaryStats(
            lhb_count=total,
            institution_net_buy_count=int(institution_net_buy_count),
            total_lhb_net_amount_wan=_wan_or_none(summary_row.total_lhb_net_amount),
        ),
    )


@router.get("/data/sectors", response_model=SectorListResponse)
def get_data_sectors(
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
) -> SectorListResponse:
    """获取当日全市场行业筛选项。"""
    industry = _industry_expr().label("industry")
    rows = db.execute(
        select(distinct(industry))
        .select_from(DailyKline)
        .outerjoin(IndexMember, IndexMember.ts_code == DailyKline.ts_code)
        .outerjoin(StockBasic, StockBasic.ts_code == DailyKline.ts_code)
        .where(DailyKline.trade_date == trade_date)
    ).scalars().all()
    sectors = sorted(str(row) for row in rows if row)
    return SectorListResponse(trade_date=str(trade_date), sectors=sectors)


@router.get("/data/stocks/{ts_code}/signals", response_model=StockSignalsResponse)
def get_stock_signals(
    ts_code: str,
    db: Annotated[Session, Depends(get_db)],
    end_date: date,
    strategy: Literal["short", "swing"] = Query(default="short"),
    days: int = Query(default=20, ge=1, le=60),
) -> StockSignalsResponse:
    """获取单股近 N 日资金流、龙虎榜和维度分。"""
    stock = db.execute(select(StockBasic).where(StockBasic.ts_code == ts_code)).scalar_one_or_none()
    member = db.execute(select(IndexMember).where(IndexMember.ts_code == ts_code)).scalar_one_or_none()
    industry = None
    if member and member.l1_name:
        industry = member.l1_name
    elif stock and stock.sw_l1:
        industry = stock.sw_l1
    elif stock and stock.industry:
        industry = stock.industry

    date_rows = db.execute(
        select(DailyKline.trade_date)
        .where(DailyKline.ts_code == ts_code)
        .where(DailyKline.trade_date <= end_date)
        .order_by(DailyKline.trade_date.desc())
        .limit(days)
    ).scalars().all()
    dates = sorted(date_rows)

    kline_rows = db.execute(
        select(DailyKline)
        .where(DailyKline.ts_code == ts_code)
        .where(DailyKline.trade_date.in_(dates))
        .order_by(DailyKline.trade_date.asc())
    ).scalars().all() if dates else []
    kline_amount_map = {row.trade_date: row.amount for row in kline_rows}

    moneyflow_rows = db.execute(
        select(Moneyflow)
        .where(Moneyflow.ts_code == ts_code)
        .where(Moneyflow.trade_date.in_(dates))
        .order_by(Moneyflow.trade_date.asc())
    ).scalars().all() if dates else []

    lhb_rows = db.execute(
        select(Lhb)
        .where(Lhb.ts_code == ts_code)
        .where(Lhb.trade_date.in_(dates))
        .order_by(Lhb.trade_date.asc())
    ).scalars().all() if dates else []

    score_rows = db.execute(
        select(FilterScoreDaily)
        .where(FilterScoreDaily.ts_code == ts_code)
        .where(FilterScoreDaily.strategy == strategy)
        .where(FilterScoreDaily.trade_date.in_(dates))
        .order_by(FilterScoreDaily.trade_date.asc(), FilterScoreDaily.dim.asc())
    ).scalars().all() if dates else []

    selection_rows = db.execute(
        select(SelectionResult)
        .where(SelectionResult.ts_code == ts_code)
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.trade_date.in_(dates))
        .order_by(SelectionResult.trade_date.asc())
    ).scalars().all() if dates else []

    return StockSignalsResponse(
        ts_code=ts_code,
        name=stock.name if stock else None,
        industry=industry,
        kline=[
            StockKlineSignal(
                trade_date=str(row.trade_date),
                close=_round_or_none(row.close),
                pct_chg=_round_or_none(row.pct_chg),
                amount=_round_or_none(row.amount),
            )
            for row in kline_rows
        ],
        moneyflow=[
            StockMoneyflowSignal(
                trade_date=str(row.trade_date),
                net_mf_wan=_round_or_none(row.net_mf_amount),
                net_mf_ratio_pct=_moneyflow_ratio(row.net_mf_amount, kline_amount_map.get(row.trade_date)),
                buy_lg_wan=_round_or_none(row.buy_lg_amount),
                sell_lg_wan=_round_or_none(row.sell_lg_amount),
                buy_elg_wan=_round_or_none(row.buy_elg_amount),
                sell_elg_wan=_round_or_none(row.sell_elg_amount),
            )
            for row in moneyflow_rows
        ],
        lhb=[
            StockLhbSignal(
                trade_date=str(row.trade_date),
                lhb_net_amount_wan=_wan_or_none(row.net_amount),
                lhb_net_rate_pct=_round_or_none(row.net_rate),
                reason=row.reason,
            )
            for row in lhb_rows
        ],
        scores=[
            StockScoreSignal(
                trade_date=str(row.trade_date),
                dim=row.dim,
                score=float(row.score),
                detail=row.detail,
            )
            for row in score_rows
        ],
        selections=[
            StockSelectionSignal(
                trade_date=str(row.trade_date),
                picked=bool(row.picked),
                rule_score=_round_or_none(row.rule_score, 1),
                final_score=_round_or_none(row.final_score, 1),
            )
            for row in selection_rows
        ],
    )


@router.get("/data/stocks/{ts_code}/lhb-seats", response_model=LhbSeatsResponse)
def get_lhb_seats(
    ts_code: str,
    db: Annotated[Session, Depends(get_db)],
    trade_date: date,
) -> LhbSeatsResponse:
    """获取单股某日龙虎榜席位明细。"""
    rows = db.execute(
        select(LhbSeatDetail)
        .where(LhbSeatDetail.ts_code == ts_code)
        .where(LhbSeatDetail.trade_date == trade_date)
        .order_by(LhbSeatDetail.seat_no.asc())
    ).scalars().all()

    return LhbSeatsResponse(
        trade_date=str(trade_date),
        ts_code=ts_code,
        seats=[
            LhbSeatItem(
                seat_no=row.seat_no,
                exalter=row.exalter,
                side=row.side,
                buy_wan=_wan_or_none(row.buy),
                sell_wan=_wan_or_none(row.sell),
                net_buy_wan=_wan_or_none(row.net_buy),
                seat_type=row.seat_type,
                reason=row.reason,
            )
            for row in rows
        ],
    )
