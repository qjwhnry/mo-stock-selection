"""综合打分：把 5 维度规则分聚合为最终 rule_score，再与 AI 分融合（Phase 3）。

**综合分公式（固定分母）**：
    final = Σ(score_i × w_i) / Σ(w_全部维度)

缺失维度按 0 计入分子但分母不变 → 严格惩罚单维霸榜，奖励多维共振。
例：仅 lhb=70（权重 0.20，全权重 1.00）→ 70 × 0.20 / 1.00 = 14
    4 维共振平均 50 → ~41，胜过单维 70。

历史变化：Phase 1 早期只有 limit + moneyflow 两维时曾用动态分母（active_weight）
保区分度，但 4 维接通后该机制让"单维度极端分"霸榜（违背多因子设计意图），
2026-04-25 改为固定分母。

**Phase 3 起**：加入 AI 分，按 `combine.rule_weight` / `combine.ai_weight` 融合。

硬规则过滤（plan §weights.yaml.hard_reject）：
- ST / *ST（可独立开关 `exclude_st`）
- 上市不足 `min_list_days` 日（可独立调整）
- 当日涨停股不推荐（避免次日追高，`exclude_today_limit_up`）
- 近 7 日负面公告关键词命中（`negative_announcement_keywords`）
"""
from __future__ import annotations

import re
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
from mo_stock.storage.models import (
    AnnsRaw,
    DailyKline,
    FilterScoreDaily,
    LimitList,
    SelectionResult,
    StockBasic,
)

# P2-6：ST 名称识别——同时覆盖 "ST"、"*ST"、半角/全角空格污染等场景
# 大小写不敏感；任意位置出现都算（兜底 is_st 字段未同步的情况）
_ST_NAME_RE = re.compile(r"\*?ST", re.IGNORECASE)


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


def _weighted_combine(
    dim_scores: dict[str, float],
    dim_weights: dict[str, float],
) -> float:
    """加权综合（固定分母）：final = Σ(score × w) / Σ(全部 w)。

    缺失维度按 0 计入（分母不缩），严格惩罚单维霸榜，奖励多维共振。
    与 `analyzer._combine_rule_score` 共用同一公式（避免分叉）。
    """
    total_w = sum(dim_weights.values())
    if total_w <= 0:
        return 0.0
    return sum(dim_scores.get(d, 0.0) * w for d, w in dim_weights.items()) / total_w


def _final_score_from(rule_score: float, ai_score: float | None,
                       rule_weight: float = 0.6, ai_weight: float = 0.4) -> float:
    """rule + ai 融合公式（P0-2 显式防御）。

    - ai_score 为 None（MVP 阶段 / AI 模块未启用）：直接返回 rule_score，
      不按权重重缩放，保持 0-100 区间稳定。
    - ai_score 为数值：按 rule_weight / ai_weight 加权平均。

    Phase 3 接入 AI 后只需保证 ai_score 有值，无需改本函数。
    """
    if ai_score is None:
        return rule_score
    total_w = rule_weight + ai_weight
    if total_w <= 0:
        return rule_score
    return (rule_score * rule_weight + ai_score * ai_weight) / total_w


def _pick_ai_candidates(
    combined: list[tuple[str, float]],
    reject_map: dict[str, str],
    ai_top_n: int,
) -> list[str]:
    """从 combined 中挑出未被硬规则淘汰的 TOP ai_top_n（v2.2 plan §3.2）。

    combined 必须已按 rule_score 降序传入。返回 ts_code 列表，按原顺序。
    """
    return [
        ts_code for ts_code, _ in combined
        if ts_code not in reject_map
    ][:ai_top_n]


def combine_scores(
    session: Session,
    trade_date: date,
    dimension_weights: dict[str, float],
    hard_reject_cfg: dict[str, Any],
    top_n: int = 20,
    enable_ai: bool = True,
    ai_top_n: int | None = None,
) -> int:
    """读当日所有维度的 filter_score_daily，做加权融合 + AI 分析 → 应用硬规则 → 取 TOP N。

    **幂等**：同一交易日重跑会 upsert `selection_result`，不会报唯一键冲突。
    综合分算法见模块 docstring（固定分母）。

    v2.2 后流程：
    1. 算 rule_score（固定分母融合 5 维）
    2. 应用硬规则得 reject_map
    3. 取未被淘汰的 TOP ai_top_n 调 AI（enable_ai=False 时跳过）
    4. _final_score_from(rule, ai) 融合（AI 缺失降级为 rule）
    5. **按 final_score 重排** → 分配 rank/picked → upsert

    Args:
        enable_ai: True → 调 AI；False → ai_score 全为 None，行为等同 v2.1
        ai_top_n: 进 AI 的候选数；None → 走 settings.top_n_after_filter（默认 50）

    Returns:
        本次真正 picked（入选 TOP N）的股票数。
    """
    from config.settings import settings

    # 解析 ai_top_n：None → settings 唯一事实源（v2.2 plan §0.2.1 第 7 条）
    effective_ai_top_n = (
        ai_top_n if ai_top_n is not None else settings.top_n_after_filter
    )

    # ---------- 1. 读当日全部维度得分，按 ts_code 聚合 ----------
    stmt = select(FilterScoreDaily).where(FilterScoreDaily.trade_date == trade_date)
    score_rows = session.execute(stmt).scalars().all()

    if not score_rows:
        logger.warning("combine_scores: {} 无任何维度打分", trade_date)
        return 0

    # stock_scores[ts_code][dim] = score （只记录 score > 0 的有效信号；
    # 缺失维度由 _weighted_combine 自动按 0 计入分子但分母不缩）
    stock_scores: dict[str, dict[str, float]] = defaultdict(dict)
    # 同时重建 dim_scores_map 给 AI prompt 用（含完整 detail）
    dim_scores_map: dict[str, dict[str, ScoreResult]] = defaultdict(dict)
    for r in score_rows:
        if r.score > 0:
            stock_scores[r.ts_code][r.dim] = r.score
        # detail 即便 score=0 也保留供 AI 参考
        dim_scores_map[r.ts_code][r.dim] = ScoreResult(
            ts_code=r.ts_code,
            trade_date=r.trade_date,
            dim=r.dim,
            score=r.score,
            detail=r.detail or {},
        )

    # ---------- 2. 按权重融合（固定分母 = 全部维度权重之和）----------
    combined: list[tuple[str, float]] = []
    for ts_code, dim_scores in stock_scores.items():
        weighted = _weighted_combine(dim_scores, dimension_weights)
        combined.append((ts_code, round(weighted, 2)))

    # ---------- 3. 应用硬规则过滤 ----------
    reject_map = _build_hard_reject_map(
        session, trade_date, hard_reject_cfg, [c[0] for c in combined]
    )

    # ---------- 4. 按 rule_score 降序，取 AI 候选 ----------
    combined.sort(key=lambda x: x[1], reverse=True)

    ai_results: dict[str, float] = {}  # {ts_code: ai_score}
    if enable_ai:
        ai_candidates = _pick_ai_candidates(combined, reject_map, effective_ai_top_n)
        logger.info(
            "combine_scores: 调 AI 分析 {} 只规则层 TOP 候选股", len(ai_candidates),
        )
        # 延迟 import 避免循环引用 + 无 AI 模块时 enable_ai=False 跑不踩雷
        from mo_stock.ai.analyzer import analyze_stock_with_ai
        for ts_code in ai_candidates:
            ai_obj = analyze_stock_with_ai(
                session, ts_code, trade_date,
                rule_dim_scores=dim_scores_map.get(ts_code, {}),
            )
            if ai_obj is not None:
                ai_results[ts_code] = ai_obj.score
        logger.info(
            "combine_scores: AI 实际成功 {} 只（候选 {}，失败 {}）",
            len(ai_results), len(ai_candidates),
            len(ai_candidates) - len(ai_results),
        )

    # ---------- 5. 融合 final_score 并按 final_score 重排（v2.2 plan §0.2.1 第 11 条）----------
    scored: list[dict[str, Any]] = []
    for ts_code, rule_score in combined:
        ai_score = ai_results.get(ts_code)  # None 时 _final_score_from 走降级
        final_score = _final_score_from(rule_score, ai_score)
        scored.append({
            "ts_code": ts_code,
            "rule_score": rule_score,
            "ai_score": ai_score,
            "final_score": round(final_score, 2),
            "reject_reason": reject_map.get(ts_code),
        })

    # **关键修正**：按 final_score 重排，而不是沿用 rule_score 排名
    scored.sort(key=lambda x: x["final_score"], reverse=True)

    rows: list[dict[str, Any]] = []
    picked_rank = 0

    for item in scored:
        reject_reason = item["reject_reason"]
        picked = reject_reason is None and picked_rank < top_n
        if picked:
            picked_rank += 1
            rank = picked_rank
        else:
            rank = 0

        # 只落库入选的，或 rule_score > 0 的未入选项（避免表过度膨胀）
        if picked or item["rule_score"] > 0:
            rows.append({
                "trade_date": trade_date,
                "ts_code": item["ts_code"],
                "rank": rank,
                "rule_score": item["rule_score"],
                "ai_score": item["ai_score"],
                "final_score": item["final_score"],
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
    - `exclude_today_limit_down` (默认 True)：当日跌停次日不抄底（跟 limit_up 对称）
    - `exclude_suspended` (默认 True)：当日停牌（amount=0 / amount 缺失）排除
    - `negative_announcement_keywords`：命中任一关键词直接淘汰
    """
    reject: dict[str, str] = {}

    if not candidates:
        return reject

    min_list_days = cfg.get("min_list_days", 60)
    exclude_st = cfg.get("exclude_st", True)
    exclude_today_limit_up = cfg.get("exclude_today_limit_up", True)
    exclude_today_limit_down = cfg.get("exclude_today_limit_down", True)
    exclude_suspended = cfg.get("exclude_suspended", True)
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

    # 2b. 当日跌停股排除（避免"机构抄底"误判，跟 limit_up 对称）
    # 用 daily_kline.pct_chg <= -9.8 判定（10% 限制取 -9.8 容差；ST 5% 限制由 exclude_st
    # 兜底；北证 30% 不在此规则范围 —— 数量极少且一般不入候选）。
    if exclude_today_limit_down:
        down_rows = session.execute(
            select(DailyKline.ts_code)
            .where(DailyKline.trade_date == trade_date)
            .where(DailyKline.ts_code.in_(candidates))
            .where(DailyKline.pct_chg.isnot(None))
            .where(DailyKline.pct_chg <= -9.8)
        ).scalars().all()
        for ts in down_rows:
            if ts not in reject:
                reject[ts] = "当日跌停，避免次日抄底"

    # 2c. P2-8：当日停牌过滤——成交额为 0 或缺失视为停牌
    # 即便 ingest 把停牌行写入了 daily_kline（pct_chg 可能为 0），amount 一定 ≈ 0；
    # 龙虎榜 / 资金流偶尔有数据残留，不应被选中。
    if exclude_suspended:
        suspended_rows = session.execute(
            select(DailyKline.ts_code)
            .where(DailyKline.trade_date == trade_date)
            .where(DailyKline.ts_code.in_(candidates))
            .where((DailyKline.amount.is_(None)) | (DailyKline.amount <= 0))
        ).scalars().all()
        for ts in suspended_rows:
            if ts not in reject:
                reject[ts] = "当日停牌（成交额为 0）"

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
    """根据股票名称判断是否 ST（兜底 is_st 字段未同步）。

    P2-6：用 regex 替代 startswith，覆盖 "*ST"、"ST"、大小写、首尾空格等场景。
    """
    if not name:
        return False
    return bool(_ST_NAME_RE.search(name))
