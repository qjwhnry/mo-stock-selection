"""板块/行业维度打分。

**思路**（见 plan §4 sector 维度）：
- 找当日**强势板块** TOP 5（按申万一级 sw_daily.pct_change 排名）
- 给所属股票加分：第 1 名板块 +50，第 5 名 +30
- 板块**近 3 日均涨幅** > 2% 加 10，> 5% 加 20（趋势加成）
- 题材增强（ths_member 命中热点概念）放在 P1，当前不实现

数据源：
- sw_daily（板块当日 + 近 3 日涨幅）
- index_member（股票 → 申万一级板块映射）

得分输出：0-100。

性能注意：
- index_member 是慢变量（月度刷新），map 一次拉全（5700 行）放进内存
- sw_daily 一级板块当日只有 31 行
- 每只股 O(1) 查 map → 算分。全市场 5500 只 → 几毫秒级
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import StockBasic


class SectorFilter(FilterBase):
    """板块强度打分器。"""

    dim = "sector"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        results: list[ScoreResult] = []

        # ---------- 1. 股票 → 一级板块映射（同时给出有效 L1 白名单）----------
        member_map = repo.get_index_member_l1_map(session)
        if not member_map:
            logger.warning("SectorFilter: index_member 表为空，无法关联个股到板块")
            return results
        valid_l1_codes = set(member_map.values())

        # ---------- 2. 当日 L1 板块涨幅 → TOP N ----------
        # 必须按 valid_l1_codes filter：sw_daily 表里 LIKE '801%' 含一/二/三级
        # 共 180 行，二三级波动比一级大很容易污染 TOP 排名
        l1_rows = repo.get_sw_daily_for_codes(session, trade_date, valid_l1_codes)
        if not l1_rows:
            logger.warning("SectorFilter: {} 当日 sw_daily L1 为空", trade_date)
            return results

        cfg = self.weights
        top_n = cfg.get("top_n_sectors", 5)
        rank_map = _top_n_l1_codes(l1_rows, n=top_n)

        # ---------- 3. 近 3 日 L1 均涨幅（趋势加成）----------
        avg_3d_map = repo.get_sw_daily_3d_avg_for_codes(
            session, trade_date, valid_l1_codes,
        )

        # ---------- 4. 候选股：所有 A 股（ST 等过滤交给 hard_reject 阶段）----------
        candidates = session.execute(select(StockBasic.ts_code)).scalars().all()

        # ---------- 5. 逐股打分 ----------
        for ts_code in candidates:
            l1_code = member_map.get(ts_code)
            if not l1_code:
                continue  # 无板块归属（新股 / 未维护）

            score = 0.0
            detail: dict[str, Any] = {"l1_code": l1_code}

            # 强势板块加分
            rank = rank_map.get(l1_code, 0)
            rank_bonus = _rank_to_bonus(rank)
            if rank_bonus > 0:
                score += rank_bonus
                detail["sector_rank"] = rank
                detail["rank_bonus"] = rank_bonus

            # 3 日均涨幅加分
            avg_3d = avg_3d_map.get(l1_code)
            if avg_3d is not None:
                trend_bonus = _three_day_avg_bonus(avg_3d)
                if trend_bonus > 0:
                    score += trend_bonus
                    detail["sector_3d_avg"] = round(avg_3d, 2)
                    detail["trend_bonus"] = trend_bonus

            # 0 分股不入 results（避免被综合分稀释，跟其它 filter 一致）
            if score > 0:
                results.append(ScoreResult(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    dim=self.dim,
                    score=clamp(score),
                    detail=detail,
                ))

        logger.info(
            "SectorFilter: {} 强势板块 {} 个，加分股 {} 只",
            trade_date, len(rank_map), len(results),
        )
        return results


# ---------------------------------------------------------------------------
# 纯函数辅助
# ---------------------------------------------------------------------------

def _top_n_l1_codes(
    rows: list[tuple[str, float | None]], n: int = 5,
) -> dict[str, int]:
    """从 [(sw_code, pct_change), ...] 取涨幅 TOP N → {sw_code: rank}（rank 从 1 起）。

    跳过 pct_change=None；同涨幅按 sw_code 字典序保证确定性。
    """
    valid = [(sc, pct) for sc, pct in rows if pct is not None]
    valid.sort(key=lambda x: (-x[1], x[0]))  # pct 降序，sw_code 升序作 tiebreaker
    return {sc: rank for rank, (sc, _) in enumerate(valid[:n], start=1)}


def _rank_to_bonus(rank: int) -> int:
    """板块涨幅排名 → 加分。TOP 5 加分，之外 0。"""
    if 1 <= rank <= 5:
        return 50 - (rank - 1) * 5  # 50, 45, 40, 35, 30
    return 0


def _three_day_avg_bonus(avg_pct: float) -> int:
    """板块近 3 日均涨幅（%）→ 加分。趋势加成。"""
    if avg_pct >= 5.0:
        return 20
    if avg_pct >= 2.0:
        return 10
    return 0
