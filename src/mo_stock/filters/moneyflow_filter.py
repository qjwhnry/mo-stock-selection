"""主力资金流向维度打分。

**思路**（见 plan §4.3）：
- 当日主力净流入 > 0 加分
- 近 3 日累计净流入 > 0 加分
- 大单+超大单占主力的比例高 → 信号更纯
- 小单大量流入但大单流出 = 负信号（散户接盘风险）

得分输出：0-100。
"""
from __future__ import annotations

from datetime import date

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo


class MoneyflowFilter(FilterBase):
    """资金流向打分器。"""

    dim = "moneyflow"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        results: list[ScoreResult] = []
        today_rows = repo.get_moneyflow(session, trade_date)

        if not today_rows:
            logger.warning("MoneyflowFilter: {} 当日资金流为空", trade_date)
            return results

        cfg = self.weights
        today_bonus = cfg.get("today_net_inflow_bonus", 20)
        rolling_bonus = cfg.get("rolling_3d_bonus", 15)
        ratio_threshold = cfg.get("big_order_ratio_threshold", 0.4)
        small_up_big_down_penalty = cfg.get("small_up_big_down_penalty", 30)

        for row in today_rows:
            score = 0.0
            detail: dict = {}

            net_mf = row.net_mf_amount or 0.0
            detail["net_mf_wan"] = round(net_mf, 2)  # 万元

            # 1. 当日主力净流入。净流出的股**不 append**，视为该维度缺失
            #    —— 避免下游 combine 聚合时被 0 分稀释 TOP 股综合分。
            if net_mf <= 0:
                continue
            score += today_bonus
            detail["today_bonus"] = today_bonus

            # 2. 大单+超大单占比
            buy_big = (row.buy_lg_amount or 0) + (row.buy_elg_amount or 0)
            sell_big = (row.sell_lg_amount or 0) + (row.sell_elg_amount or 0)
            big_net = buy_big - sell_big
            total_turnover = abs(buy_big) + abs(sell_big) + 1.0  # 避免除 0
            big_ratio = big_net / total_turnover
            detail["big_ratio"] = round(big_ratio, 3)

            if big_ratio >= ratio_threshold:
                # 占比越高，越纯粹的机构/游资行为
                ratio_bonus = min(25.0, big_ratio * 50)
                score += ratio_bonus
                detail["ratio_bonus"] = round(ratio_bonus, 1)

            # 3. 近 3 日滚动累计
            series = repo.get_moneyflow_series(session, row.ts_code, trade_date, days=3)
            rolling_sum = sum((m.net_mf_amount or 0) for m in series)
            detail["rolling_3d_wan"] = round(rolling_sum, 2)
            if rolling_sum > 0:
                score += rolling_bonus
                detail["rolling_bonus"] = rolling_bonus

            # 4. 负信号：小单净流入但大单净流出
            buy_sm = row.buy_sm_amount or 0
            sell_sm = row.sell_sm_amount or 0
            small_net = buy_sm - sell_sm
            if small_net > 0 and big_net < 0:
                score -= small_up_big_down_penalty
                detail["small_up_big_down_penalty"] = -small_up_big_down_penalty

            # 20 分基础分给首次净流入转正的股票（让"弱转强"也有一定分数）
            score = max(score, 20.0)

            results.append(ScoreResult(
                ts_code=row.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(score),
                detail=detail,
            ))

        logger.info("MoneyflowFilter: {} 共 {} 只股票主力净流入为正", trade_date, len(results))
        return results
