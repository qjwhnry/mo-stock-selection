"""综合打分：把 5 维度规则分聚合为最终 rule_score，再与 AI 分融合（Phase 3）。

**当前 Phase 1 MVP**：只有 limit + moneyflow 两个维度，其他维度缺失时按 0 处理。
**Phase 3 起**：加入 AI 分，按 `combine.rule_weight` / `combine.ai_weight` 融合。

硬规则过滤（plan §weights.yaml.hard_reject）：
- ST / *ST
- 上市不足 60 日
- 当日涨停股不推荐（避免次日追高）
- 近 7 日负面公告关键词命中
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.data_sources.calendar import is_selectable
from mo_stock.filters.base import ScoreResult
from mo_stock.storage import repo
from mo_stock.storage.models import AnnsRaw, FilterScoreDaily, LimitList, SelectionResult, StockBasic


def persist_filter_scores(session: Session, results: list[ScoreResult]) -> int:
    """把每个维度的打分批量 upsert 入 filter_score_daily。"""
    rows: list[dict[str, Any]] = [
        {
            "trade_date": r.trade_date,
            "ts_code": r.ts_code,
            "dim": r.dim,
            "score": r.score,
            "detail": r.detail,
        }
        for r in results
    ]

    if not rows:
        return 0

    return repo.upsert_rows(
        session,
        FilterScoreDaily,
        rows,
        conflict_cols=["trade_date", "ts_code", "dim"],
        update_cols=["score", "detail"],
    )


def combine_scores(
    session: Session,
    trade_date: date,
    dimension_weights: dict[str, float],
    hard_reject_cfg: dict[str, Any],
    top_n: int = 20,
) -> list[SelectionResult]:
    """读当日所有维度的 filter_score_daily，做加权融合 → 应用硬规则 → 取 TOP N。

    MVP 阶段 AI 分为 None，final_score = rule_score。

    Returns:
        本次产出的 SelectionResult 列表（已写入数据库）。
    """
    # ---------- 1. 读当日全部维度得分，按 ts_code 聚合 ----------
    stmt = select(FilterScoreDaily).where(FilterScoreDaily.trade_date == trade_date)
    score_rows = session.execute(stmt).scalars().all()

    if not score_rows:
        logger.warning("combine_scores: {} 无任何维度打分", trade_date)
        return []

    # stock_scores[ts_code][dim] = score
    stock_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for r in score_rows:
        stock_scores[r.ts_code][r.dim] = r.score

    # ---------- 2. 按权重融合 ----------
    total_weight = sum(dimension_weights.values()) or 1.0  # 归一化
    combined: list[tuple[str, float]] = []

    for ts_code, dim_scores in stock_scores.items():
        weighted = sum(
            dim_scores.get(dim, 0.0) * w for dim, w in dimension_weights.items()
        ) / total_weight
        combined.append((ts_code, round(weighted, 2)))

    # ---------- 3. 应用硬规则过滤 ----------
    reject_map = _build_hard_reject_map(session, trade_date, hard_reject_cfg, [c[0] for c in combined])

    # ---------- 4. 排序，写入 selection_result ----------
    combined.sort(key=lambda x: x[1], reverse=True)

    results: list[SelectionResult] = []
    picked_rank = 0

    for ts_code, rule_score in combined:
        reject_reason = reject_map.get(ts_code)
        picked = reject_reason is None and picked_rank < top_n

        if picked:
            picked_rank += 1
            rank = picked_rank
        else:
            # 未入选的股票也记一行（picked=False），便于复盘为什么没进
            rank = 0

        sel = SelectionResult(
            trade_date=trade_date,
            ts_code=ts_code,
            rank=rank,
            rule_score=rule_score,
            ai_score=None,               # MVP 阶段没有 AI 分
            final_score=rule_score,      # Phase 3 会改为 rule*0.6 + ai*0.4
            picked=picked,
            reject_reason=reject_reason,
        )
        results.append(sel)

        # 只落库入选的，或分数 > 0 的未入选项（避免表过度膨胀）
        if picked or rule_score > 0:
            session.merge(sel)

    logger.info(
        "combine_scores {}: 聚合 {} 只 → 入选 TOP {}（硬规则淘汰 {} 只）",
        trade_date,
        len(combined),
        picked_rank,
        len(reject_map),
    )
    # 只返回真正入选的，供报告层使用
    return [r for r in results if r.picked]


# ---------------------------------------------------------------------------
# 硬规则
# ---------------------------------------------------------------------------

def _build_hard_reject_map(
    session: Session,
    trade_date: date,
    cfg: dict[str, Any],
    candidates: list[str],
) -> dict[str, str]:
    """为候选股构建 {ts_code: 拒绝理由} 映射。未入选的股票不在此表。"""
    reject: dict[str, str] = {}

    if not candidates:
        return reject

    min_list_days = cfg.get("min_list_days", 60)
    exclude_st = cfg.get("exclude_st", True)
    exclude_today_limit_up = cfg.get("exclude_today_limit_up", True)
    neg_keywords: list[str] = cfg.get("negative_announcement_keywords", [])

    # 1. 股票基础过滤（ST / 次新）
    basics = session.execute(
        select(StockBasic).where(StockBasic.ts_code.in_(candidates))
    ).scalars().all()
    for b in basics:
        ok, reason = is_selectable(b, trade_date, min_list_days=min_list_days)
        if not ok and exclude_st:
            reject[b.ts_code] = reason

    # 2. 当日涨停股排除（避免追高）
    if exclude_today_limit_up:
        limit_rows = session.execute(
            select(LimitList.ts_code)
            .where(LimitList.trade_date == trade_date)
            .where(LimitList.limit_type == "U")
            .where(LimitList.ts_code.in_(candidates))
        ).scalars().all()
        for ts in limit_rows:
            if ts not in reject:
                reject[ts] = "当日涨停，避免次日追高"

    # 3. 近 7 日负面公告关键词命中
    if neg_keywords:
        from datetime import timedelta
        start = trade_date - timedelta(days=7)
        anns = session.execute(
            select(AnnsRaw.ts_code, AnnsRaw.title)
            .where(AnnsRaw.ts_code.in_(candidates))
            .where(AnnsRaw.ann_date.between(start, trade_date))
        ).all()
        for ts_code, title in anns:
            if ts_code in reject:
                continue
            if any(kw in (title or "") for kw in neg_keywords):
                reject[ts_code] = f"负面公告：{title[:30]}"

    return reject
