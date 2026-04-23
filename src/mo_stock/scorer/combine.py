"""综合打分：把 5 维度规则分聚合为最终 rule_score，再与 AI 分融合（Phase 3）。

**当前 Phase 1 MVP**：只有 limit + moneyflow 两个维度，其他维度缺失时**按"该股未覆盖"处理**
（从分母中扣除，不拉低综合分），这样 MVP 阶段 TOP N 的综合分仍有良好区分度。

**Phase 3 起**：加入 AI 分，按 `combine.rule_weight` / `combine.ai_weight` 融合。

硬规则过滤（plan §weights.yaml.hard_reject）：
- ST / *ST（可独立开关 `exclude_st`）
- 上市不足 `min_list_days` 日（可独立调整）
- 当日涨停股不推荐（避免次日追高，`exclude_today_limit_up`）
- 近 7 日负面公告关键词命中（`negative_announcement_keywords`）
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
) -> int:
    """读当日所有维度的 filter_score_daily，做加权融合 → 应用硬规则 → 取 TOP N。

    **幂等**：同一交易日重跑会 upsert `selection_result`，不会报唯一键冲突。

    综合分算法：
        ``final = Σ(score_i × w_i) / Σ(w_i)  # 只对该股实际有分的维度 i 累加``

    这样 MVP 阶段（只有 limit + moneyflow 有分）综合分仍然能到接近 100，不会被
    lhb/sector/sentiment 0 分拉低到 30 左右。

    MVP 阶段 AI 分为 None，final_score = rule_score。

    Returns:
        本次真正 picked（入选 TOP N）的股票数。
    """
    # ---------- 1. 读当日全部维度得分，按 ts_code 聚合 ----------
    stmt = select(FilterScoreDaily).where(FilterScoreDaily.trade_date == trade_date)
    score_rows = session.execute(stmt).scalars().all()

    if not score_rows:
        logger.warning("combine_scores: {} 无任何维度打分", trade_date)
        return 0

    # stock_scores[ts_code][dim] = score （只记录有效得分，score > 0）
    stock_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for r in score_rows:
        # score = 0 视为"该维度信号缺失"，不参与加权（避免被 0 分稀释）
        # 真正的"一票否决"走 hard_reject，不依赖 0 分
        if r.score > 0:
            stock_scores[r.ts_code][r.dim] = r.score

    # ---------- 2. 按权重融合（动态分母）----------
    combined: list[tuple[str, float]] = []

    for ts_code, dim_scores in stock_scores.items():
        active_weight = sum(w for d, w in dimension_weights.items() if d in dim_scores)
        if active_weight <= 0:
            continue  # 权重配置里没这几个维度
        weighted = (
            sum(dim_scores[d] * w for d, w in dimension_weights.items() if d in dim_scores)
            / active_weight
        )
        combined.append((ts_code, round(weighted, 2)))

    # ---------- 3. 应用硬规则过滤 ----------
    reject_map = _build_hard_reject_map(
        session, trade_date, hard_reject_cfg, [c[0] for c in combined]
    )

    # ---------- 4. 排序 → 准备 upsert 数据 ----------
    combined.sort(key=lambda x: x[1], reverse=True)

    rows: list[dict[str, Any]] = []
    picked_rank = 0

    for ts_code, rule_score in combined:
        reject_reason = reject_map.get(ts_code)
        picked = reject_reason is None and picked_rank < top_n

        if picked:
            picked_rank += 1
            rank = picked_rank
        else:
            rank = 0

        # 只落库入选的，或 score > 0 的未入选项（避免表过度膨胀）
        if picked or rule_score > 0:
            rows.append({
                "trade_date": trade_date,
                "ts_code": ts_code,
                "rank": rank,
                "rule_score": rule_score,
                "ai_score": None,               # MVP 阶段
                "final_score": rule_score,      # Phase 3 后改为 rule*0.6 + ai*0.4
                "picked": picked,
                "reject_reason": reject_reason,
            })

    # ---------- 5. upsert 到 selection_result（幂等）----------
    if rows:
        ins = pg_insert(SelectionResult).values(rows)
        excluded = ins.excluded
        ins = ins.on_conflict_do_update(
            index_elements=["trade_date", "ts_code"],
            set_={
                "rank": excluded.rank,
                "rule_score": excluded.rule_score,
                "ai_score": excluded.ai_score,
                "final_score": excluded.final_score,
                "picked": excluded.picked,
                "reject_reason": excluded.reject_reason,
            },
        )
        session.execute(ins)

    logger.info(
        "combine_scores {}: 聚合 {} 只 → 入选 TOP {}（硬规则淘汰 {} 只）",
        trade_date,
        len(combined),
        picked_rank,
        len(reject_map),
    )
    return picked_rank


# ---------------------------------------------------------------------------
# 硬规则
# ---------------------------------------------------------------------------

def _build_hard_reject_map(
    session: Session,
    trade_date: date,
    cfg: dict[str, Any],
    candidates: list[str],
) -> dict[str, str]:
    """为候选股构建 {ts_code: 拒绝理由} 映射。未入选的股票不在此表。

    硬规则独立开关，互不耦合：
    - `exclude_st` (默认 True)：是否排除 ST/*ST
    - `min_list_days` (默认 60)：上市不满 N 日的次新过滤；设 0 或负值则不过滤
    - `exclude_today_limit_up` (默认 True)：当日涨停次日不追高
    - `negative_announcement_keywords`：命中任一关键词直接淘汰
    """
    reject: dict[str, str] = {}

    if not candidates:
        return reject

    min_list_days = cfg.get("min_list_days", 60)
    exclude_st = cfg.get("exclude_st", True)
    exclude_today_limit_up = cfg.get("exclude_today_limit_up", True)
    neg_keywords: list[str] = cfg.get("negative_announcement_keywords", [])

    # 1. 股票基础过滤（ST 与 次新 独立开关）
    if exclude_st or min_list_days > 0:
        basics = session.execute(
            select(StockBasic).where(StockBasic.ts_code.in_(candidates))
        ).scalars().all()
        for b in basics:
            # ST 判断
            if exclude_st and (b.is_st or _name_is_st(b.name)):
                reject[b.ts_code] = "ST / *ST"
                continue
            # 次新判断
            if min_list_days > 0 and b.list_date:
                days_listed = (trade_date - b.list_date).days
                if days_listed < min_list_days:
                    reject[b.ts_code] = f"上市仅 {days_listed} 日，不足 {min_list_days} 日"
                    continue
            # 兜底：调用现有 is_selectable 保留原有判定（未来扩展点）
            if exclude_st and min_list_days > 0:
                ok, reason = is_selectable(b, trade_date, min_list_days=min_list_days)
                if not ok and b.ts_code not in reject:
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


def _name_is_st(name: str | None) -> bool:
    """根据股票名称判断是否 ST（兜底 is_st 字段可能未同步的情况）。"""
    if not name:
        return False
    upper = name.upper()
    return "ST" in upper or name.startswith("*ST")
