"""龙虎榜维度打分（v2.1 plan §3.4：base 60 + seat 40 重排）。

**思路**：
- 数据源：lhb 表（Tushare top_list）做 base，lhb_seat_detail 表（top_inst）做 seat
- base 上限 60：base_score 20 + tier(0-20) + purity(0-12) + reason(0-8)
- seat 上限 40 / 下限 -15：
  - 机构净买 ≥ 1000 万 → +20（远比 net_rate 更强信号）
  - 知名游资净买 ≥ 500 万 → +12
  - 知名游资净卖 ≥ 1000 万 → -15（量化 / 一日游风险）
  - 北向净买 ≥ 3000 万 → +8
- 跌幅榜（"跌幅偏离" / "跌幅达"）reason 整股跳过

得分输出：0-100。

**口径变更**：从 v1 的"base 0-100"改成 v2.1 的"base 60 + seat 40"。
detail 中含 `lhb_formula_version=2`，无该字段的旧分数不与新分数横比。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import LhbSeatDetail


class LhbFilter(FilterBase):
    """龙虎榜打分器（v2.1 base + seat 双层结构）。"""

    dim = "lhb"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        results: list[ScoreResult] = []
        rows = repo.get_lhb_today(session, trade_date)
        if not rows:
            logger.info("LhbFilter: {} 当日无龙虎榜数据", trade_date)
            return results

        # 一次性查席位明细，按 ts_code 分组（缺失则 [] 不影响打分）
        seats_map = repo.get_lhb_seats_today(session, trade_date)

        cfg = self.weights
        base_score_value = cfg.get("base_score", 20)
        skipped_drop = 0

        for r in rows:
            # 跌幅榜上榜整股跳过（跌停反弹与"找强势股"目标矛盾）
            if _is_drop_rebound_reason(r.reason):
                skipped_drop += 1
                continue

            # 净卖出 / 字段缺失 → 视为该维度信号缺失，不入 results
            if r.net_rate is None or r.net_rate <= 0:
                continue

            # base (上限 60)
            tier = _net_rate_tier_bonus(r.net_rate)
            purity = _purity_bonus(r.amount_rate)
            reason_b = _reason_bonus(r.reason)
            score = float(base_score_value) + tier + purity + reason_b

            detail: dict[str, Any] = {
                "lhb_formula_version": 2,
                "net_rate_pct": round(r.net_rate, 2),
                "amount_rate_pct": round(r.amount_rate or 0, 2),
                "reason": r.reason,
                "net_rate_tier_bonus": tier,
                "purity_bonus": purity,
                "reason_bonus": reason_b,
            }

            # seat (上限 40, 下限 -15)
            seats = seats_map.get(r.ts_code, [])
            seat_delta, seat_detail = _seat_structure_score(seats, cfg)
            score += seat_delta
            detail.update(seat_detail)

            results.append(ScoreResult(
                ts_code=r.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(score),
                detail=detail,
            ))

        logger.info(
            "LhbFilter: {} 共 {} 只股入选（跳过跌幅榜 {} 只）",
            trade_date, len(results), skipped_drop,
        )
        return results


# ---------------------------------------------------------------------------
# base 子项纯函数（v2.1 重排：上限 20 / 12 / 8）
# ---------------------------------------------------------------------------

def _net_rate_tier_bonus(net_rate_pct: float | None) -> int:
    """龙虎榜净买入占当日总成交比例（%）→ 加分。

    v2.1 阈值：2% → 10、5% → 15、10% → 20（上限 20）。
    """
    if net_rate_pct is None or net_rate_pct <= 0:
        return 0
    if net_rate_pct >= 10.0:
        return 20
    if net_rate_pct >= 5.0:
        return 15
    if net_rate_pct >= 2.0:
        return 10
    return 0


def _purity_bonus(amount_rate_pct: float | None) -> int:
    """龙虎榜成交占当日总成交比例（%）→ 加分。

    v2.1 阈值：15% → 6、30% → 12（上限 12）。
    """
    if amount_rate_pct is None or amount_rate_pct <= 0:
        return 0
    if amount_rate_pct >= 30.0:
        return 12
    if amount_rate_pct >= 15.0:
        return 6
    return 0


# v2.1 关键词权重：连续三日涨幅 8（最强），其它涨幅类 5
_REASON_KEYWORD_SCORES: list[tuple[str, int]] = [
    ("连续三日涨幅", 8),
    ("无价格涨跌幅限制", 5),
    ("日涨幅偏离", 5),
    ("日换手率", 5),
]

# 跌幅榜上榜关键词，命中即整股跳过
_DROP_REASON_KEYWORDS: tuple[str, ...] = (
    "跌幅偏离",
    "跌幅达",
)


def _reason_bonus(reason: str | None) -> int:
    """上榜原因文本 → 加分。多原因取最大值。仅涨幅类加分。"""
    if not reason:
        return 0
    return max(
        (score for kw, score in _REASON_KEYWORD_SCORES if kw in reason),
        default=0,
    )


def _is_drop_rebound_reason(reason: str | None) -> bool:
    """识别跌幅榜上榜（命中即整股跳过）。"""
    if not reason:
        return False
    return any(kw in reason for kw in _DROP_REASON_KEYWORDS)


# ---------------------------------------------------------------------------
# seat 部分（v2.1 新增，上限 40 / 下限 -15）
# ---------------------------------------------------------------------------

def _seat_structure_score(
    seats: list[LhbSeatDetail], cfg: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    """龙虎榜席位结构加减分。

    seat_type 可叠加：
    - institution: 净买 ≥ min_net_buy_yuan → +bonus
    - hot_money_buy: 净买 ≥ min_net_buy_yuan → +bonus
    - hot_money_sell: 净卖 ≥ min_net_sell_yuan → -penalty
    - northbound_buy: 净买 ≥ min_net_buy_yuan → +bonus

    上限 40 / 下限 -15（仅 hot_money_sell 一项可扣，且 base ≥ 20 不会负总分）。
    """
    if not seats:
        return 0.0, {}

    inst_net = sum((s.net_buy or 0) for s in seats if s.seat_type == "institution")
    hot_net  = sum((s.net_buy or 0) for s in seats if s.seat_type == "hot_money")
    nb_net   = sum((s.net_buy or 0) for s in seats if s.seat_type == "northbound")

    score = 0.0
    detail: dict[str, Any] = {}

    inst_cfg = cfg.get("institution", {})
    if inst_net >= inst_cfg.get("min_net_buy_yuan", 10_000_000):
        bonus = inst_cfg.get("bonus", 20)
        score += bonus
        detail["institution_net_buy"] = round(inst_net, 0)
        detail["institution_bonus"] = bonus

    hm_buy_cfg = cfg.get("hot_money_buy", {})
    if hot_net >= hm_buy_cfg.get("min_net_buy_yuan", 5_000_000):
        bonus = hm_buy_cfg.get("bonus", 12)
        score += bonus
        detail["hot_money_net_buy"] = round(hot_net, 0)
        detail["hot_money_bonus"] = bonus

    hm_sell_cfg = cfg.get("hot_money_sell", {})
    if hot_net <= -hm_sell_cfg.get("min_net_sell_yuan", 10_000_000):
        penalty = hm_sell_cfg.get("penalty", 15)
        score -= penalty
        detail["hot_money_sell_penalty"] = -penalty

    nb_cfg = cfg.get("northbound_buy", {})
    if nb_net >= nb_cfg.get("min_net_buy_yuan", 30_000_000):
        bonus = nb_cfg.get("bonus", 8)
        score += bonus
        detail["northbound_net_buy"] = round(nb_net, 0)
        detail["northbound_bonus"] = bonus

    # 上限 40 / 下限 -15
    score = max(-15.0, min(40.0, score))
    return score, detail
