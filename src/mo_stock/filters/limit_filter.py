"""涨停异动维度打分。

**思路**（见 plan §4.2）：
- 首板加基础分，连板首日（第 2 板）加更多
- 封板时间早 / 封单额大 / 打开次数少 → 加分
- 炸板 ≥ 2 次直接置 0
- **硬规则**：当日涨停股不作为"次日追高买入"推荐，仅作板块信号（在 scorer 层过滤）

得分输出：0-100
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo


class LimitFilter(FilterBase):
    """涨停异动打分器。"""

    dim = "limit"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        """对当日涨停股打分。

        策略：仅对当日出现在 limit_list 的股票打分；其他股票该维度为 0。
        """
        results: list[ScoreResult] = []
        limit_rows = repo.get_limit_list(session, trade_date, limit_type="U")

        if not limit_rows:
            logger.info("LimitFilter: {} 当日无涨停", trade_date)
            return results

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

        logger.info("LimitFilter: {} 打分 {} 只涨停", trade_date, len(results))
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
