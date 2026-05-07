"""题材/概念维度打分（与 sector 平级独立维度）。

**思路**：
- 数据源：ths_daily（概念涨幅）+ limit_concept_daily（涨停最强）+
  ths_concept_moneyflow（资金确认）
- 多概念股**取最高**概念加分，不累加（避免沾边股霸榜）
- 同一概念内：涨幅/涨停信号折扣叠加，资金流按排名分位加减分，
  再叠加短线轻量时间信号；跨概念取 max：

```
score(stock) = max over concepts of:
    max(ths_bonus, limit_bonus) + discount * min(ths_bonus, limit_bonus)
    + moneyflow_rank_bonus
    + temporal_bonus
```

**渐进降级**（v2.1 修法）：ths_daily 为空时仍跑 limit_concept + moneyflow，
只有三类信号都空才提前返回。

得分输出：0-100。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo


class ThemeFilter(FilterBase):
    """同花顺概念 + 涨停最强 + 资金流三合一题材打分。"""

    dim = "theme"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        cfg = self.weights
        top_n = cfg.get("top_n_themes", 10)
        ths_rank_table: dict[int, int] = cfg.get("ths_rank_bonus", {})
        limit_rank_table: dict[int, int] = cfg.get("limit_concept_rank_bonus", {})
        signal_discount = float(cfg.get("signal_discount", 0.3))
        max_bonus = cfg.get("max_theme_bonus", 100)

        # 1. THS 概念涨幅 TOP N → {concept_code: rank}
        top_themes = repo.get_top_ths_themes(session, trade_date, n=top_n)
        ths_rank_map = {t.ts_code: i for i, t in enumerate(top_themes, start=1)}

        if not top_themes:
            logger.warning(
                "ThemeFilter: {} 无 ths_daily 数据，将继续使用 limit_cpt_list / "
                "moneyflow_cnt_ths 信号", trade_date,
            )

        # 2. 涨停最强概念 → {concept_code: rank}
        limit_rank_map = repo.get_limit_concept_rank_map(session, trade_date)

        # 3. 概念资金流 → {concept_code: net_amount}
        moneyflow_map = repo.get_concept_moneyflow_map(session, trade_date)
        moneyflow_bonus_map = _moneyflow_bonus_map(moneyflow_map, cfg)

        # 4. 昨日 THS 概念涨幅 TOP N，用于短线轻量时间信号
        prev_trade_date = _previous_trade_date(session, trade_date)
        prev_rank_map: dict[str, int] = {}
        if prev_trade_date:
            prev_themes = repo.get_top_ths_themes(session, prev_trade_date, n=top_n)
            prev_rank_map = {
                t.ts_code: i for i, t in enumerate(prev_themes, start=1)
            }

        # 5. 三类主信号都为空时提前返回
        if not ths_rank_map and not limit_rank_map and not moneyflow_map:
            logger.warning("ThemeFilter: {} 三类题材信号均为空", trade_date)
            return []

        # 6. 股票 → 概念列表（慢变量，全表扫一次）
        stock_concepts = repo.get_stock_to_concepts_map(session)

        results: list[ScoreResult] = []
        for ts_code, concepts in stock_concepts.items():
            best = 0.0
            best_concept: str | None = None
            best_detail: dict[str, Any] = {}
            for c in concepts:
                rb = _bonus_from_table(ths_rank_table, ths_rank_map.get(c, 0))
                lb = _bonus_from_table(limit_rank_table, limit_rank_map.get(c, 0))
                mb = moneyflow_bonus_map.get(c, 0)
                tb, temporal_detail = _temporal_bonus(
                    c, ths_rank_map, prev_rank_map, cfg,
                )
                signal_score = max(rb, lb) + signal_discount * min(rb, lb)
                total = signal_score + mb + tb
                if total > best:
                    best = total
                    best_concept = c
                    best_detail = {
                        "ths_bonus": rb,
                        "limit_bonus": lb,
                        "moneyflow_bonus": mb,
                        "temporal_bonus": tb,
                        **temporal_detail,
                    }
            if best <= 0:
                continue
            score = clamp(min(best, max_bonus))
            detail: dict[str, Any] = {
                "best_concept": best_concept,
                "ths_rank": ths_rank_map.get(best_concept or "", 0),
                "limit_rank": limit_rank_map.get(best_concept or "", 0),
                "concept_net_amount_yi": round(moneyflow_map.get(best_concept or "", 0.0), 2),
                **best_detail,
            }
            results.append(ScoreResult(
                ts_code=ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=score,
                detail=detail,
            ))

        logger.info("ThemeFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


# ---------------------------------------------------------------------------
# 纯函数辅助
# ---------------------------------------------------------------------------

def _bonus_from_table(table: dict[int, int], rank: int) -> int:
    """从分档表查 rank 对应分数。table 形如 {1: 50, 2: 42, 3: 35, 5: 22, 10: 12}。

    策略：找 >= rank 的最小 key 对应的分数（而非精确等于）。
    rank=4 时若表里只有 1/2/3/5，返回 5 对应分数（保守）。
    rank=0 或 rank > max(keys) → 0。
    """
    if rank <= 0 or not table:
        return 0
    sorted_keys = sorted(table.keys())
    max_key = sorted_keys[-1]
    if rank > max_key:
        return 0
    for k in sorted_keys:
        if k >= rank:
            return table[k]
    return 0


def _moneyflow_bonus_map(
    moneyflow_map: dict[str, float],
    cfg: dict[str, Any],
) -> dict[str, int]:
    """概念资金流排名分位 → 加减分。

    新配置使用 concept_moneyflow；缺失时兼容旧配置
    concept_moneyflow_positive_bonus。
    """
    if not moneyflow_map:
        return {}

    table = cfg.get("concept_moneyflow")
    if not table:
        legacy_bonus = int(cfg.get("concept_moneyflow_positive_bonus", 15))
        return {
            code: legacy_bonus
            for code, net_amount in moneyflow_map.items()
            if net_amount > 0
        }

    result: dict[str, int] = {}
    positive = [
        (code, amount) for code, amount in moneyflow_map.items() if amount > 0
    ]
    positive.sort(key=lambda item: (-item[1], item[0]))
    for rank, (code, _amount) in enumerate(positive, start=1):
        score = _rank_score_from_config(
            table,
            "inflow_positive_rank_top_",
            rank,
        )
        if score:
            result[code] = score

    negative = [
        (code, amount) for code, amount in moneyflow_map.items() if amount < 0
    ]
    negative.sort(key=lambda item: (item[1], item[0]))
    for rank, (code, _amount) in enumerate(negative, start=1):
        score = _rank_score_from_config(
            table,
            "outflow_negative_rank_bot_",
            rank,
        )
        if score:
            result[code] = score

    return result


def _rank_score_from_config(
    table: dict[str, dict[str, int]],
    prefix: str,
    rank: int,
) -> int:
    """读取形如 inflow_positive_rank_top_5 的配置，rank 落在最小可覆盖档位。"""
    tiers: list[tuple[int, int]] = []
    for key, value in table.items():
        if not key.startswith(prefix):
            continue
        try:
            threshold = int(key.removeprefix(prefix))
        except ValueError:
            continue
        tiers.append((threshold, int(value.get("score", 0))))
    if not tiers:
        return 0
    for threshold, score in sorted(tiers):
        if rank <= threshold:
            return score
    return 0


def _temporal_bonus(
    concept_code: str,
    today_rank_map: dict[str, int],
    prev_rank_map: dict[str, int],
    cfg: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """昨日上榜和排名改善的小额短线确认。"""
    table = cfg.get("concept_temporal_bonus", {})
    today_rank = today_rank_map.get(concept_code)
    prev_rank = prev_rank_map.get(concept_code)
    if not today_rank or not prev_rank:
        return 0, {}

    bonus = 0
    detail: dict[str, Any] = {"prev_ths_rank": prev_rank}
    if table.get("yesterday_in_top_n"):
        bonus += int(table["yesterday_in_top_n"])
        detail["yesterday_in_top_n_bonus"] = int(table["yesterday_in_top_n"])
    if today_rank < prev_rank and table.get("rank_improvement"):
        bonus += int(table["rank_improvement"])
        detail["rank_improvement_bonus"] = int(table["rank_improvement"])
    return bonus, detail


def _previous_trade_date(session: Session, trade_date: date) -> date | None:
    """优先用交易日历取昨日；测试库无交易日历时退化为自然日前一日。"""
    dates = repo.get_recent_trade_dates(session, trade_date, 2)
    if len(dates) >= 2:
        return dates[1]
    return trade_date - timedelta(days=1)
