"""涨停异动维度打分（PLAN §4.2 设计：limit 维度只作为个股涨停 + 反包信号）。

**核心思路**：当日涨停股会被 hard_reject.exclude_today_limit_up 过滤，所以 LimitFilter
真正能进 TOP 20 的得分股是「跟涨停相关但今天非涨停」的断板反包股：

1. **断板反包**（PLAN.md 原文「首板 > 连板首日 > 断板反包」）：
   - 昨天涨停 + 今天没涨停 + 今天涨幅 ≥ 1%（保持强势）
   - 这种股 hard_reject 不过滤，是 limit 维度真正的"产出"
   - 涨幅梯度加分：1~3% / 3~5% / 5~8% / ≥8% 四档

2. **当日涨停股**（保留打分供 analyzer 单股查询用）：
   - 原有逻辑：连板/封单/早封/开板。但综合分会被 hard_reject 让 picked=False。

得分输出：0-100。

**v2.3 修正（audit-sector-concentration-2026-04-28）**：
旧版本曾有第 3 类"同板块涨停热度溢出"（给同板块所有非涨停股加 60 分），
此机制与 `sector` 维度（TOP1 板块 +70）形成多重共线性，导致 1 个板块强势同时拉高
4 个维度，最终 Top N 全部来自同一板块。已删除，板块热度信号集中在 `sector` 维度。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline, StockBasic

_ONE_DAY = timedelta(days=1)


class LimitFilter(FilterBase):
    """涨停异动 + 断板反包打分器（v2.3 起，板块热度信号集中到 sector 维度）。"""

    dim = "limit"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        """三类股的 limit 维度得分（详见模块 docstring）。"""
        results: list[ScoreResult] = []

        # ---------- 准备数据 ----------
        # 1) 当日涨停股
        today_limit_codes = repo.get_limit_up_codes(session, trade_date)
        # 2) 昨日涨停股（断板反包候选源）
        yesterday = trade_date - _ONE_DAY
        yesterday_limit_codes = repo.get_limit_up_codes(session, yesterday)
        # 3) 当日 K 线（断板反包要看 pct_chg）
        kline_pct_rows = session.execute(
            select(DailyKline.ts_code, DailyKline.pct_chg)
            .where(DailyKline.trade_date == trade_date),
        ).all()
        kline_pct_map: dict[str, float | None] = {
            cast(str, row[0]): cast("float | None", row[1])
            for row in kline_pct_rows
        }

        # ---------- 1. 当日涨停股原有打分（保留供 analyzer 用） ----------
        limit_rows = repo.get_limit_list(session, trade_date, limit_type="U")
        if not limit_rows:
            logger.info("LimitFilter: {} 当日无涨停", trade_date)

        # 读取细则参数（来自 weights.yaml 的 limit_filter 节）
        cfg = self.weights
        first_board_bonus = cfg.get("first_board_bonus", 20)
        second_board_bonus = cfg.get("second_board_bonus", 30)
        open_penalty = cfg.get("open_times_penalty", 10)
        seal_tiers = cfg.get("seal_amount_tier", [])

        for row in limit_rows:
            detail: dict[str, Any] = {}
            score = 0.0

            # 连板数：Tushare 有 up_stat（"连板数/总上榜数"）或 limit_times
            limit_times = row.limit_times or self._parse_limit_times(row.up_stat)
            detail["limit_times"] = limit_times

            # 炸板 ≥ 2 次硬淘汰
            open_times = row.open_times or 0
            detail["open_times"] = open_times
            if open_times >= 2:
                detail["hard_fail"] = "炸板≥2次"
                results.append(ScoreResult(row.ts_code, trade_date, self.dim, 0.0, detail))
                continue

            # 连板分档：首板 / 连板首日（2 板）/ 更高板数再加
            if limit_times == 1:
                score += first_board_bonus
                detail["board_bonus"] = ("首板", first_board_bonus)
            elif limit_times == 2:
                score += second_board_bonus
                detail["board_bonus"] = ("2 连板", second_board_bonus)
            elif limit_times >= 3:
                # 高连板风险大，加分递减
                bonus = second_board_bonus - (limit_times - 2) * 5
                score += max(bonus, 10)
                detail["board_bonus"] = (f"{limit_times} 连板", max(bonus, 10))

            # 封单额分档：越大越强
            fd_amount_yi = (row.fd_amount or 0) / 1e8  # 元 → 亿元
            detail["fd_amount_yi"] = round(fd_amount_yi, 2)
            seal_bonus = 0.0
            for tier in seal_tiers:
                if fd_amount_yi >= tier["threshold"]:
                    seal_bonus = tier["score"]
            score += seal_bonus
            detail["seal_bonus"] = seal_bonus

            # 开板扣分（炸板 1 次还能救）
            if open_times > 0:
                score -= open_penalty * open_times
                detail["open_penalty"] = -open_penalty * open_times

            # 首次封板时间：越早越好
            if row.first_time:
                bonus = self._first_time_bonus(row.first_time)
                score += bonus
                detail["first_time"] = row.first_time
                detail["first_time_bonus"] = bonus

            results.append(ScoreResult(
                ts_code=row.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(score),
                detail=detail,
            ))

        # ---------- 2. 断板反包（核心产出） ----------
        # v2.3：移除"板块涨停热度溢出"以消除与 sector 维度的多重共线性。
        # 候选股集合：所有 A 股，扣除「今日已经在 #1 打分的涨停股」
        all_stocks = session.execute(select(StockBasic.ts_code)).scalars().all()
        rebound_count = 0

        for ts_code in all_stocks:
            if ts_code in today_limit_codes:
                continue  # 今日涨停股已在上面打分

            score = 0.0
            detail = {}

            # 断板反包：昨涨停今没涨停但保持涨势
            today_pct = kline_pct_map.get(ts_code)
            yesterday_was_limit = ts_code in yesterday_limit_codes
            rebound = _break_board_rebound_bonus(
                yesterday_was_limit, today_is_limit_up=False, today_pct_chg=today_pct,
            )
            if rebound > 0:
                score += rebound
                detail["break_board_rebound"] = rebound
                detail["yesterday_limit_up"] = True
                detail["today_pct_chg"] = round(today_pct or 0, 2)
                rebound_count += 1

            if score > 0:
                results.append(ScoreResult(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    dim=self.dim,
                    score=clamp(score),
                    detail=detail,
                ))

        logger.info(
            "LimitFilter: {} 涨停打分 {} 只 + 断板反包 {} 只",
            trade_date, len(limit_rows), rebound_count,
        )
        return results

    # ---------- 辅助 ----------

    @staticmethod
    def _parse_limit_times(up_stat: str | None) -> int:
        """解析 up_stat 如 '2/3' → 2（最近 2 连板）。"""
        if not up_stat:
            return 1
        try:
            return int(up_stat.split("/")[0])
        except (ValueError, IndexError):
            return 1

    @staticmethod
    def _first_time_bonus(first_time: str) -> float:
        """首次封板时间 → 加分。

        - 09:30-10:00 封板：+15（强势板）
        - 10:00-11:00：+10
        - 11:00-13:30：+5
        - 13:30 之后：0
        """
        try:
            hh, mm, *_ = first_time.split(":")
            minutes = int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            return 0

        if minutes <= 10 * 60:
            return 15
        if minutes <= 11 * 60:
            return 10
        if minutes <= 13 * 60 + 30:
            return 5
        return 0


# ---------------------------------------------------------------------------
# 模块级纯函数：断板反包 + 板块涨停溢出
# ---------------------------------------------------------------------------

def _break_board_rebound_bonus(
    yesterday_was_limit_up: bool,
    today_is_limit_up: bool,
    today_pct_chg: float | None,
) -> int:
    """断板反包加分：昨涨停今没涨停但保持涨势。

    PLAN.md 指定的核心场景：「首板 > 连板首日 > 断板反包」。
    断板反包股 hard_reject 不会过滤（今天非涨停），是 limit 维度真正能进 TOP 的来源。

    满分 100：基础 30（断板有效）+ 涨幅梯度 0-70（1~3% / 3~5% / 5~8% / ≥8%）。
    """
    if not yesterday_was_limit_up or today_is_limit_up:
        return 0  # 不是断板（昨没涨停，或今天又涨停 = 连板）
    if today_pct_chg is None or today_pct_chg < 1.0:
        return 0  # 今天涨幅太小（< 1%），不算反包

    # 基础分 30 + 涨幅梯度
    if today_pct_chg >= 8.0:
        return 100
    if today_pct_chg >= 5.0:
        return 70
    if today_pct_chg >= 3.0:
        return 50
    return 30  # 1~3%
