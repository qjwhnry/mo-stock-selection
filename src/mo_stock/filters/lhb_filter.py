"""龙虎榜维度打分。

**思路**（见 plan §4 lhb 维度）：
- 数据源：lhb 表（Tushare top_list），仅对当日上榜股打分
- 核心信号：席位净买入 > 0 = 游资/机构看多
- 加分维度（**全部用 Tushare 现成的占比字段**，跨股可比）：
  1. net_rate（净买入占当日总成交 %）—— 替代绝对金额绝避免大盘股偏弱、小盘股偏强
  2. amount_rate（席位成交占当日总成交 %）—— 反映席位主导度
  3. 上榜原因 reason —— 仅涨幅/换手类加分；跌幅类整股跳过
- 反向：net_rate ≤ 0（净卖出）/ 跌幅榜上榜 → 不加分（视为该维度信号缺失）

得分输出：0-100。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo


class LhbFilter(FilterBase):
    """龙虎榜打分器。"""

    dim = "lhb"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        """对当日上榜股打分。其它股票该维度视为缺失（combine 动态分母处理）。"""
        results: list[ScoreResult] = []
        rows = repo.get_lhb_today(session, trade_date)

        if not rows:
            logger.info("LhbFilter: {} 当日无龙虎榜数据", trade_date)
            return results

        skipped_drop = 0
        for r in rows:
            # 跌幅榜上榜（跌停反弹机构抄底）跟"找强势股"目标矛盾，整股跳过
            if _is_drop_rebound_reason(r.reason):
                skipped_drop += 1
                continue

            net_rate = r.net_rate  # Tushare 现成字段：净买入占当日总成交 %
            # 净卖出（net_rate <= 0）/ 字段缺失 → 视为该维度信号缺失，不入 results
            if net_rate is None or net_rate <= 0:
                continue

            score = 30.0  # 基础分：上榜（涨幅类）且净买入
            detail: dict[str, Any] = {
                "net_rate_pct": round(net_rate, 2),
                "amount_rate_pct": round(r.amount_rate or 0, 2),
                "reason": r.reason,
            }

            tier = _net_rate_tier_bonus(net_rate)
            score += tier
            detail["net_rate_tier_bonus"] = tier

            purity = _purity_bonus(r.amount_rate)
            score += purity
            detail["purity_bonus"] = purity

            reason_b = _reason_bonus(r.reason)
            score += reason_b
            detail["reason_bonus"] = reason_b

            results.append(ScoreResult(
                ts_code=r.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(score),
                detail=detail,
            ))

        logger.info(
            "LhbFilter: {} 共 {} 只股净买入上榜（额外跳过 {} 只跌幅榜）",
            trade_date, len(results), skipped_drop,
        )
        return results


# ---------------------------------------------------------------------------
# 纯函数辅助：分档 + 关键词加分
# ---------------------------------------------------------------------------

def _net_rate_tier_bonus(net_rate_pct: float | None) -> int:
    """龙虎榜净买入占当日总成交比例（%，Tushare 现成字段 net_rate）→ 加分。

    跨股可比：跟绝对金额相比，占比能消除「大盘股净额大但占比小」的偏差。
    阈值：2% / 5% / 10% 三档。赤天化 600227 真实 3.36% → +15。
    上限 30（占维度满分 100 的 30%，与基础 30 + purity 25 + reason 15 共凑 100）。
    """
    if net_rate_pct is None or net_rate_pct <= 0:
        return 0
    if net_rate_pct >= 10.0:
        return 30
    if net_rate_pct >= 5.0:
        return 22
    if net_rate_pct >= 2.0:
        return 15
    return 0


def _purity_bonus(amount_rate_pct: float | None) -> int:
    """龙虎榜成交占当日总成交比例（%，Tushare 现成字段 amount_rate）→ 加分。

    比例越高 = 上榜资金主导今日成交 = 信号越纯（避免散户对倒虚假活跃）。
    阈值：15% / 30% 两档。上限 25。
    """
    if amount_rate_pct is None or amount_rate_pct <= 0:
        return 0
    if amount_rate_pct >= 30.0:
        return 25
    if amount_rate_pct >= 15.0:
        return 12
    return 0


# 上榜原因关键词 → 分数。仅涨幅/换手类加分，跌幅类由 _is_drop_rebound_reason 单独识别后整股跳过
# 上限 15（占维度满分 100 的 15%）。
_REASON_KEYWORD_SCORES: list[tuple[str, int]] = [
    ("连续三日涨幅", 15),
    ("无价格涨跌幅限制", 8),
    ("日涨幅偏离", 8),
    ("日换手率", 8),
]

# 跌幅榜上榜关键词。命中即整股跳过（跌停反弹策略跟"找强势股"目标矛盾）
_DROP_REASON_KEYWORDS: tuple[str, ...] = (
    "跌幅偏离",   # "日跌幅偏离值达 7%" / "连续三日跌幅偏离值达 20%"
    "跌幅达",     # "无价格涨跌幅限制日跌幅达 30%"
)


def _reason_bonus(reason: str | None) -> int:
    """上榜原因文本 → 加分。多原因取最大值。仅涨幅类才加分。"""
    if not reason:
        return 0
    return max(
        (score for kw, score in _REASON_KEYWORD_SCORES if kw in reason),
        default=0,
    )


def _is_drop_rebound_reason(reason: str | None) -> bool:
    """识别"跌幅榜上榜"。命中则整股不入 LhbFilter 结果（避免机构抄底跌停股污染选股）。"""
    if not reason:
        return False
    return any(kw in reason for kw in _DROP_REASON_KEYWORDS)
