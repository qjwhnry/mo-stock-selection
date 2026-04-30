"""题材/概念维度打分（v2.1 plan §3.3，与 sector 平级独立维度）。

**思路**：
- 数据源：ths_daily（概念涨幅）+ limit_concept_daily（涨停最强）+
  ths_concept_moneyflow（资金确认）
- 多概念股**取最高**概念加分，不累加（避免沾边股霸榜）
- 三类信号在同一概念上 sum，跨概念 max：

```
score(stock) = max over concepts of:
    rank_bonus(ths_pct_change_rank) +
    limit_concept_bonus(limit_rank) +
    moneyflow_bonus(net_amount > 0)
```

**渐进降级**（v2.1 修法）：ths_daily 为空时仍跑 limit_concept + moneyflow，
只有三类信号都空才提前返回。

得分输出：0-100。
"""
from __future__ import annotations

from datetime import date
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
        moneyflow_bonus_value = cfg.get("concept_moneyflow_positive_bonus", 15)
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

        # 4. 三类信号都为空时提前返回
        if not ths_rank_map and not limit_rank_map and not moneyflow_map:
            logger.warning("ThemeFilter: {} 三类题材信号均为空", trade_date)
            return []

        # 5. 股票 → 概念列表（慢变量，全表扫一次）
        stock_concepts = repo.get_stock_to_concepts_map(session)

        results: list[ScoreResult] = []
        for ts_code, concepts in stock_concepts.items():
            best = 0
            best_concept: str | None = None
            for c in concepts:
                rb = _bonus_from_table(ths_rank_table, ths_rank_map.get(c, 0))
                lb = _bonus_from_table(limit_rank_table, limit_rank_map.get(c, 0))
                mb = moneyflow_bonus_value if moneyflow_map.get(c, 0.0) > 0 else 0
                total = rb + lb + mb
                if total > best:
                    best = total
                    best_concept = c
            if best <= 0:
                continue
            score = clamp(min(best, max_bonus))
            detail: dict[str, Any] = {
                "best_concept": best_concept,
                "ths_rank": ths_rank_map.get(best_concept or "", 0),
                "limit_rank": limit_rank_map.get(best_concept or "", 0),
                "concept_net_amount_yi": round(moneyflow_map.get(best_concept or "", 0.0), 2),
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
