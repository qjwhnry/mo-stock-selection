"""涨停异动维度打分（PLAN §4.2 设计：limit 维度只作为个股涨停 + 反包信号）。

**核心思路**：当日涨停股会被 hard_reject.exclude_today_limit_up 过滤，所以 LimitFilter
真正能进 TOP 20 的得分股是「跟涨停相关但今天非涨停」的断板反包股：

1. **断板反包**（PLAN.md 原文「首板 > 连板首日 > 断板反包」）：
   - 昨天涨停 + 今天没涨停 + 今天涨幅 ≥ 1%（保持强势）
   - 这种股 hard_reject 不过滤，是 limit 维度真正的"产出"
   - 涨幅梯度加分：1~3% / 3~5% / 5~8% / ≥8% 四档
   - **v2.5**：连板位置感知（board_position）+ 冲高回落检测（close_position）

2. **当日涨停股**（保留打分供 analyzer 单股查询用）：
   - 原有逻辑：连板/封单/早封/开板。但综合分会被 hard_reject 让 picked=False。

得分输出：0-100。

**v2.3 修正（audit-sector-concentration-2026-04-28）**：
旧版本曾有第 3 类"同板块涨停热度溢出"（给同板块所有非涨停股加 60 分），
此机制与 `sector` 维度（TOP1 板块 +70）形成多重共线性，导致 1 个板块强势同时拉高
4 个维度，最终 Top N 全部来自同一板块。已删除，板块热度信号集中在 `sector` 维度。
"""
from __future__ import annotations

from datetime import date
from typing import Any, cast

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.data_sources.calendar import classify_market, previous_trading_day
from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.filters.short.limit_context import (
    get_prev_limit_times_map,
    parse_limit_times,
)
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline, StockBasic


class LimitFilter(FilterBase):
    """涨停异动 + 断板反包打分器（v2.3 起，板块热度信号集中到 sector 维度）。"""

    dim = "limit"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        """三类股的 limit 维度得分（详见模块 docstring）。"""
        results: list[ScoreResult] = []

        # ---------- 准备数据 ----------
        # 1) 当日涨停股
        today_limit_codes = repo.get_limit_up_codes(session, trade_date)
        # 2) 上一交易日涨停股（断板反包候选源）。不能用自然日 -1，
        # 否则周一 / 长假后会查到非交易日，漏掉真正的断板反包。
        prev_trade_date = previous_trading_day(session, trade_date)
        if prev_trade_date is None:
            logger.warning("LimitFilter: {} 找不到上一交易日，跳过断板反包", trade_date)
            yesterday_limit_codes: set[str] = set()
            prev_limit_times_map: dict[str, int] = {}
        else:
            yesterday_limit_codes = repo.get_limit_up_codes(session, prev_trade_date)
            prev_limit_times_map = get_prev_limit_times_map(session, prev_trade_date)

        # 3) 当日 K 线（断板反包需要 pct_chg + OHLCV 做冲高回落检测）
        kline_rows = session.execute(
            select(
                DailyKline.ts_code, DailyKline.pct_chg,
                DailyKline.open, DailyKline.high, DailyKline.low,
                DailyKline.close, DailyKline.pre_close,
            )
            .where(DailyKline.trade_date == trade_date),
        ).all()
        kline_pct_map: dict[str, float | None] = {}
        ohlc_map: dict[str, dict[str, float | None]] = {}
        for kline_row in kline_rows:
            ts = cast(str, kline_row[0])
            kline_pct_map[ts] = cast("float | None", kline_row[1])
            ohlc_map[ts] = {
                "open": kline_row[2],
                "high": kline_row[3],
                "low": kline_row[4],
                "close": kline_row[5],
                "pre_close": kline_row[6],
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

        for limit_row in limit_rows:
            limit_detail: dict[str, Any] = {}
            score = 0.0

            # 连板数：Tushare 有 up_stat（"连板数/总上榜数"）或 limit_times
            limit_times = parse_limit_times(limit_row.limit_times, limit_row.up_stat)
            limit_detail["limit_times"] = limit_times

            # 炸板 ≥ 2 次硬淘汰
            open_times = limit_row.open_times or 0
            limit_detail["open_times"] = open_times
            if open_times >= 2:
                limit_detail["hard_fail"] = "炸板≥2次"
                results.append(ScoreResult(limit_row.ts_code, trade_date, self.dim, 0.0, limit_detail))
                continue

            # 连板分档：首板 / 连板首日（2 板）/ 更高板数再加
            if limit_times == 1:
                score += first_board_bonus
                limit_detail["board_bonus"] = ("首板", first_board_bonus)
            elif limit_times == 2:
                score += second_board_bonus
                limit_detail["board_bonus"] = ("2 连板", second_board_bonus)
            elif limit_times >= 3:
                # 高连板风险大，加分递减
                bonus = second_board_bonus - (limit_times - 2) * 5
                score += max(bonus, 10)
                limit_detail["board_bonus"] = (f"{limit_times} 连板", max(bonus, 10))

            # 封单额分档：越大越强
            fd_amount_yi = (limit_row.fd_amount or 0) / 1e8  # 元 → 亿元
            limit_detail["fd_amount_yi"] = round(fd_amount_yi, 2)
            seal_bonus = 0.0
            for tier in seal_tiers:
                if fd_amount_yi >= tier["threshold"]:
                    seal_bonus = tier["score"]
            score += seal_bonus
            limit_detail["seal_bonus"] = seal_bonus

            # 开板扣分（炸板 1 次还能救）
            if open_times > 0:
                score -= open_penalty * open_times
                limit_detail["open_penalty"] = -open_penalty * open_times

            # 首次封板时间：越早越好
            if limit_row.first_time:
                bonus = self._first_time_bonus(limit_row.first_time)
                score += bonus
                limit_detail["first_time"] = limit_row.first_time
                limit_detail["first_time_bonus"] = bonus

            results.append(ScoreResult(
                ts_code=limit_row.ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(score),
                detail=limit_detail,
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
            rebound_detail: dict[str, Any] = {}

            # 断板反包：昨涨停今没涨停但保持涨势
            today_pct = kline_pct_map.get(ts_code)
            yesterday_was_limit = ts_code in yesterday_limit_codes
            # 改进 A：连板序列感知
            board_position = prev_limit_times_map.get(ts_code, 1)
            rebound = _break_board_rebound_bonus(
                yesterday_was_limit, today_is_limit_up=False,
                today_pct_chg=today_pct, board_position=board_position,
            )
            if rebound > 0:
                score += rebound
                rebound_detail["break_board_rebound"] = rebound
                rebound_detail["board_position"] = board_position
                rebound_detail["yesterday_limit_up"] = True
                rebound_detail["today_pct_chg"] = round(today_pct or 0, 2)

                # 改进 C：冲高回落检测
                ohlc = ohlc_map.get(ts_code, {})
                fade = _near_limit_up_fade_bonus(
                    ts_code=ts_code,
                    today_high=ohlc.get("high"),
                    today_low=ohlc.get("low"),
                    today_close=ohlc.get("close"),
                    pre_close=ohlc.get("pre_close"),
                )
                if fade < 0:
                    score += fade
                    rebound_detail["near_limit_fade"] = fade

                rebound_count += 1

            if score > 0:
                results.append(ScoreResult(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    dim=self.dim,
                    score=clamp(score),
                    detail=rebound_detail,
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
# 模块级纯函数：断板反包 + 冲高回落检测
# ---------------------------------------------------------------------------

def _limit_up_ratio(ts_code: str) -> float:
    """按股票代码/板块返回涨停幅度。

    主板（60xxxx / 00xxxx）：1.10
    创业板（300xxx / 301xxx）：1.20
    科创板（688xxx / 689xxx）：1.20
    北交所（.BJ / 8xxxxx / 4xxxxx）：1.30
    ST 不在本函数处理——已被 hard_reject（exclude_st）过滤，不会进入打分流程。
    """
    board = classify_market(ts_code)
    if board in {"创业板", "科创板"}:
        return 1.20
    if board == "北交所":
        return 1.30
    return 1.10


def _near_limit_up_threshold(pre_close: float, limit_up_ratio: float) -> float:
    """近涨停阈值 = 涨停价 * 0.99（距涨停 1% 以内）。"""
    return pre_close * limit_up_ratio * 0.99


def _near_limit_up_fade_bonus(
    ts_code: str,
    today_high: float | None,
    today_low: float | None,
    today_close: float | None,
    pre_close: float | None,
) -> int:
    """当日近涨停未封 + 收盘在全天振幅下半区 → 冲高回落负分。

    条件：
    1. high >= 动态近涨停阈值（距该股涨停价 1% 以内）
    2. 收盘在全天振幅的下半区（close_position <= 0.5）

    返回 0 或 -20。
    """
    if (
        today_high is None
        or today_low is None
        or today_close is None
        or pre_close is None
    ):
        return 0

    near_limit_threshold = _near_limit_up_threshold(
        pre_close, _limit_up_ratio(ts_code),
    )
    near_limit = today_high >= near_limit_threshold

    day_range = today_high - today_low
    if day_range <= 0:
        return 0  # 一字板 / 无波动，不惩罚

    close_position = (today_close - today_low) / day_range
    fade = close_position <= 0.5

    return -20 if (near_limit and fade) else 0


def _break_board_rebound_bonus(
    yesterday_was_limit_up: bool,
    today_is_limit_up: bool,
    today_pct_chg: float | None,
    board_position: int = 1,
) -> int:
    """断板反包加分：昨涨停今没涨停但保持涨势。

    board_position 越大，断板信号越偏空：
    - board_position = 1: 首板后断板，不变
    - board_position = 2: 二连板后断板，各档 -20
    - board_position >= 3: 三连板以上，封顶 40
    """
    if not yesterday_was_limit_up or today_is_limit_up:
        return 0
    if today_pct_chg is None or today_pct_chg < 1.0:
        return 0

    if board_position <= 1:
        bonus_table = {8.0: 100, 5.0: 70, 3.0: 50, 1.0: 30}
    elif board_position == 2:
        bonus_table = {8.0: 80, 5.0: 50, 3.0: 30, 1.0: 15}
    else:
        bonus_table = {8.0: 40, 5.0: 30, 3.0: 20, 1.0: 10}

    for threshold, bonus in bonus_table.items():
        if today_pct_chg >= threshold:
            return bonus
    return 0
