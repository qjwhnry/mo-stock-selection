"""主力资金流向维度打分。

**思路**（见 plan §4.3）：
- 当日主力净流入占成交额比例 → 占比分档（替代绝对金额一刀切，跨股可比）
- 近 3 日累计净流入 > 0 加分
- 大单+超大单占主力的比例高 → 信号更纯
- 小单大量流入但大单流出 = 负信号（散户接盘风险）

得分输出：0-100。

# 资金流双口径警告（P2-7）

本 filter 同时使用 Tushare moneyflow 接口的两套互不相同的统计字段：

| 信号       | 字段                                | 口径含义                      |
|----------|-----------------------------------|---------------------------|
| today_bonus | `net_mf_amount`                   | **主动主力**净流入（按交易方向算法判定） |
| ratio_bonus | `buy_lg + buy_elg − sell_lg − sell_elg` | **全量大单**净流入（按单笔金额机械分档）|

两套口径**不能直接相加**，可能给出对立信号。判读建议：
- `today_bonus 大 + big_ratio 小`：散户主动跟风，警惕追高陷阱
- `today_bonus 小 + big_ratio 大`：机构悄悄建仓，低调买入
- 两者同向放大：信号最强（rolling_3d_bonus 兜底）

详细对比见 [docs/两种主力资金统计口径对比 & 实战选用指南.md](../../../docs/两种主力资金统计口径对比 & 实战选用指南（详细版）.md)。
"""
from __future__ import annotations

from datetime import date

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline


class MoneyflowFilter(FilterBase):
    """资金流向打分器。"""

    dim = "moneyflow"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        results: list[ScoreResult] = []
        today_rows = repo.get_moneyflow(session, trade_date)

        if not today_rows:
            logger.warning("MoneyflowFilter: {} 当日资金流为空", trade_date)
            return results

        # 一次拉取当日全部 daily_kline.amount（千元），避免逐股 query
        # SQL 已 filter NOT NULL，但 dict() 推导器内显式 if 也帮 mypy 收紧类型
        kline_amount_map: dict[str, float] = {
            ts: amt
            for ts, amt in session.execute(
                select(DailyKline.ts_code, DailyKline.amount)
                .where(DailyKline.trade_date == trade_date)
                .where(DailyKline.amount.isnot(None)),
            ).all()
            if amt is not None
        }

        cfg = self.weights
        rolling_bonus = cfg.get("rolling_3d_bonus", 15)
        ratio_threshold = cfg.get("big_order_ratio_threshold", 0.4)
        small_up_big_down_penalty = cfg.get("small_up_big_down_penalty", 30)
        rolling_sum_map = repo.get_moneyflow_rolling_sum_map(session, trade_date, days=3)

        for row in today_rows:
            score = 0.0
            detail: dict = {}

            net_mf_wan = row.net_mf_amount or 0.0  # 万元
            detail["net_mf_wan"] = round(net_mf_wan, 2)

            # 1. 主力净流入占当日成交比例 → 占比分档。净流出/缺失视为该维度信号缺失。
            kline_amt_qy = kline_amount_map.get(row.ts_code)  # 千元
            today_bonus = _today_bonus_tier(net_mf_wan, kline_amt_qy)
            if not net_mf_wan or net_mf_wan <= 0:
                # 净流出或缺失 → 无资金流信号，不入 results
                continue
            score += today_bonus
            if today_bonus > 0:
                detail["today_bonus"] = round(today_bonus, 2)
            if kline_amt_qy:
                ratio_pct = 1000.0 * net_mf_wan / kline_amt_qy
                detail["net_mf_ratio_pct"] = round(ratio_pct, 3)

            # 2. 大单+超大单占比
            buy_big = (row.buy_lg_amount or 0) + (row.buy_elg_amount or 0)
            sell_big = (row.sell_lg_amount or 0) + (row.sell_elg_amount or 0)
            big_net = buy_big - sell_big
            total_turnover = abs(buy_big) + abs(sell_big) + 1.0  # 避免除 0
            big_ratio = big_net / total_turnover
            detail["big_ratio"] = round(big_ratio, 3)

            if big_ratio >= ratio_threshold:
                # 占比越高，越纯粹的机构/游资行为；上限 30
                ratio_bonus = min(30.0, big_ratio * 60)
                score += ratio_bonus
                detail["ratio_bonus"] = round(ratio_bonus, 1)

            # 3. 近 3 日滚动累计（批量预取，避免逐股 N+1 查询）
            rolling_sum = rolling_sum_map.get(row.ts_code, 0.0)
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

            final_score = clamp(score)
            if final_score <= 0:
                continue
            results.append(ScoreResult(
                ts_code=row.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=final_score,
                detail=detail,
            ))

        logger.info(
            "MoneyflowFilter: {} 共 {} 只股票主力净流入达阈值",
            trade_date, len(results),
        )
        return results


# ---------------------------------------------------------------------------
# 纯函数辅助：占比分档
# ---------------------------------------------------------------------------

def _today_bonus_tier(
    net_mf_wan: float | None, daily_amount_qy: float | None,
) -> float:
    """主力净流入占当日成交额比例（%）→ 连续加分。

    单位换算：moneyflow.net_mf_amount 是万元，daily_kline.amount 是千元。
        ratio (%) = 1000 × net_mf_wan / amount_qy

    v2.4 连续化：线性插值替代离散分档，提升同板块内个股区分度。
    ratio < 0.3% → 0（信号太弱）
    0.3% ≤ r < 5% → 5 + (r - 0.3) / 4.7 * 40（线性，≈每 1% 多 8.5 分）
    r ≥ 5%        → 50（封顶）
    """
    if not net_mf_wan or net_mf_wan <= 0:
        return 0
    if not daily_amount_qy or daily_amount_qy <= 0:
        return 0
    ratio_pct = 1000.0 * net_mf_wan / daily_amount_qy
    if ratio_pct < 0.3:
        return 0
    if ratio_pct >= 5.0:
        return 50.0
    return 5.0 + (ratio_pct - 0.3) / 4.7 * 40.0
