"""板块/行业维度打分。

**思路**：
- 从申万一级（31 个）下沉到申万二级（~100 个），粒度更精细
- 找当日强势 L2 TOP N，只给行业内**领涨股**加分，过滤跟涨股
- 领涨条件（满足 ≥2 项）：涨幅前 30% / 成交额前 30% / 量比 ≥ 1.5
- 小样本 L2（成分 <5）跳过领涨判断，行业强弱本身已足够作为信号
- 原始分（rank 0-50 + trend 0-20 = 最高 70）归一化到 0-100

数据源：
- sw_daily（板块当日涨跌幅 + 近 3 日均涨幅）
- index_member（股票 → L2 板块映射，l2_code 字段）
- daily_kline（个股当日涨幅 pct_chg / 成交额 amount / 成交量 vol）

得分输出：0-100。原始分上限 70（rank 50 + trend 20），经 _normalize_sector_score 归一化。

性能注意：
- index_member 是慢变量（月度刷新），map 一次拉全（5700 行）放进内存
- sw_daily L2 板块当日约 100 行
- daily_kline 查 21 日数据计算量比（1 次批量查询）
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from math import ceil
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline


class SectorFilter(FilterBase):
    """板块强度打分器。"""

    dim = "sector"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        results: list[ScoreResult] = []

        # ---------- 1. 股票 → 二级板块映射（同时给出有效 L2 白名单）----------
        member_map = repo.get_index_member_l2_map(session)
        if not member_map:
            logger.warning("SectorFilter: index_member L2 为空，无法关联个股到板块")
            return results
        valid_l2_codes = set(member_map.values())

        # ---------- 2. 当日 L2 板块涨幅 → TOP N ----------
        l2_rows = repo.get_sw_daily_for_codes(session, trade_date, valid_l2_codes)
        if not l2_rows:
            logger.warning("SectorFilter: {} 当日 sw_daily L2 为空", trade_date)
            return results

        cfg = self.weights
        top_n = int(cfg.get("top_n_l2", 10))
        rank_map = _top_n_codes(l2_rows, n=top_n)

        # ---------- 3. 近 3 日 L2 均涨幅（趋势加成）----------
        avg_3d_map = repo.get_sw_daily_3d_avg_for_codes(
            session, trade_date, valid_l2_codes,
        )

        # ---------- 4. 个股行业内领涨上下文 ----------
        context = _build_stock_sector_context(session, trade_date, member_map, cfg)

        # ---------- 5. 逐股打分 ----------
        for ts_code, l2_code in member_map.items():
            score = 0.0
            detail: dict[str, Any] = {"l2_code": l2_code}

            # 强势板块加分
            rank = rank_map.get(l2_code, 0)
            rank_bonus = _rank_to_bonus(rank)
            if rank_bonus > 0:
                leadership = context.leadership_factor.get(ts_code, 0.0)
                if leadership > 0:
                    score += rank_bonus * leadership
                    detail["sector_rank"] = rank
                    detail["rank_bonus"] = rank_bonus
                    detail["leadership_factor"] = leadership
                    detail.update(context.stock_detail.get(ts_code, {}))

            # 3 日均涨幅加分
            avg_3d = avg_3d_map.get(l2_code)
            if avg_3d is not None and rank_bonus > 0:
                trend_bonus = _three_day_avg_bonus(avg_3d)
                leadership = context.leadership_factor.get(ts_code, 0.0)
                if trend_bonus > 0 and leadership > 0:
                    score += trend_bonus * leadership
                    detail["sector_3d_avg"] = round(avg_3d, 2)
                    detail["trend_bonus"] = trend_bonus

            # 0 分股不入 results（避免被综合分稀释，跟其它 filter 一致）
            if score > 0:
                results.append(ScoreResult(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    dim=self.dim,
                    score=_normalize_sector_score(score),
                    detail=detail,
                ))

        logger.info(
            "SectorFilter: {} 强势 L2 {} 个，出分股 {} 只",
            trade_date, len(rank_map), len(results),
        )
        return results


# ---------------------------------------------------------------------------
# 纯函数辅助
# ---------------------------------------------------------------------------

class _SectorContext:
    """预计算的个股行业内上下文，避免逐股重复查询。

    leadership_factor: {ts_code: 1.0 或 0.0}，领涨=1.0，跟涨=0.0
    stock_detail: {ts_code: {pct_leader, amount_leader, volume_ratio_leader, ...}}
    vol_ratio_by_stock: {ts_code: 当日量 / 20日均量}
    """

    def __init__(
        self,
        leadership_factor: dict[str, float],
        stock_detail: dict[str, dict[str, Any]],
        vol_ratio_by_stock: dict[str, float],
    ) -> None:
        self.leadership_factor = leadership_factor
        self.stock_detail = stock_detail
        self.vol_ratio_by_stock = vol_ratio_by_stock


def _top_n_l1_codes(
    rows: list[tuple[str, float | None]], n: int = 5,
) -> dict[str, int]:
    """兼容旧测试命名；逻辑已泛化为任意 sw_code。"""
    return _top_n_codes(rows, n=n)


def _top_n_codes(
    rows: list[tuple[str, float | None]], n: int = 5,
) -> dict[str, int]:
    """从 [(sw_code, pct_change), ...] 取涨幅 TOP N → {sw_code: rank}（rank 从 1 起）。

    跳过 pct_change=None；同涨幅按 sw_code 字典序保证确定性。
    """
    valid = [(sc, pct) for sc, pct in rows if pct is not None]
    valid.sort(key=lambda x: (-x[1], x[0]))  # pct 降序，sw_code 升序作 tiebreaker
    return {sc: rank for rank, (sc, _) in enumerate(valid[:n], start=1)}


def _rank_to_bonus(rank: int) -> int:
    """板块涨幅排名 → 加分。TOP 10 加分，之外 0。

    分档策略：找 ≥ rank 的最小 key（保守，与 theme_filter._bonus_from_table 一致）。
    """
    rank_table = {1: 50, 2: 40, 3: 35, 4: 28, 5: 22, 7: 14, 10: 8}
    if rank <= 0:
        return 0
    for k in sorted(rank_table):
        if k >= rank:
            return rank_table[k]
    return 0


def _three_day_avg_bonus(avg_pct: float) -> int:
    """板块近 3 日均涨幅（%）→ 加分。趋势加成。v2.4 降档。"""
    if avg_pct >= 5.0:
        return 20
    if avg_pct >= 2.0:
        return 10
    return 0


def _build_stock_sector_context(
    session: Session,
    trade_date: date,
    member_map: dict[str, str],
    cfg: dict[str, Any],
) -> _SectorContext:
    """预计算个股在 L2 行业内的领涨因子。

    步骤：
    1. 查当日 daily_kline，按 L2 行业分组
    2. 查近 21 日 daily_kline 计算每只股的 20 日均量比
    3. 对每个 L2 行业，判断每只股是否满足领涨条件（≥2 项）：
       a. 涨幅在行业内前 30%
       b. 成交额在行业内前 30%
       c. 量比 ≥ 1.5（相对自身 20 日均量）
    4. 小样本行业（成分 < min_l2_size_for_leadership）跳过，全部给 1.0
    """
    today_rows = session.execute(
        select(
            DailyKline.ts_code,
            DailyKline.pct_chg,
            DailyKline.amount,
            DailyKline.vol,
        )
        .where(DailyKline.trade_date == trade_date)
        .where(DailyKline.ts_code.in_(member_map.keys()))
    ).all()

    dates = repo.get_recent_trade_dates(session, trade_date, 21)
    if not dates:
        dates = [trade_date]
    vol_rows = session.execute(
        select(DailyKline.ts_code, DailyKline.trade_date, DailyKline.vol)
        .where(DailyKline.trade_date.in_(dates))
        .where(DailyKline.ts_code.in_(member_map.keys()))
    ).all()

    vol_history: dict[str, list[float]] = defaultdict(list)
    today_vol: dict[str, float] = {}
    for ts_code, row_date, vol in vol_rows:
        if vol is None:
            continue
        if row_date == trade_date:
            today_vol[ts_code] = vol
        else:
            vol_history[ts_code].append(vol)

    vol_ratio_by_stock: dict[str, float] = {}
    for ts_code, vol in today_vol.items():
        history = vol_history.get(ts_code, [])
        if history:
            avg_vol = sum(history) / len(history)
            if avg_vol > 0:
                vol_ratio_by_stock[ts_code] = vol / avg_vol

    by_l2: dict[str, list[tuple[str, float | None, float | None]]] = defaultdict(list)
    stock_detail: dict[str, dict[str, Any]] = {}
    for ts_code, pct_chg, amount, _vol in today_rows:
        l2_code = member_map.get(ts_code)
        if not l2_code:
            continue
        by_l2[l2_code].append((ts_code, pct_chg, amount))
        stock_detail[ts_code] = {}
        if pct_chg is not None:
            stock_detail[ts_code]["stock_pct_chg"] = round(pct_chg, 2)
        if ts_code in vol_ratio_by_stock:
            stock_detail[ts_code]["volume_ratio_20d"] = round(
                vol_ratio_by_stock[ts_code], 2,
            )

    leadership_factor: dict[str, float] = {}
    min_size = int(cfg.get("min_l2_size_for_leadership", 5))
    for _l2_code, rows in by_l2.items():
        pct_rows = [(ts, pct) for ts, pct, _amount in rows if pct is not None]
        amount_rows = [(ts, amount) for ts, _pct, amount in rows if amount is not None]

        size = len(rows)
        if size < min_size:
            for ts_code, _pct, _amount in rows:
                leadership_factor[ts_code] = 1.0
                stock_detail.setdefault(ts_code, {})["small_l2_skip_leadership"] = True
            continue

        threshold_count = _top_count(size)
        if size <= 10:
            threshold_count = min(size, max(threshold_count, 3))
        pct_leaders = _top_stock_set(pct_rows, threshold_count)
        amount_leaders = _top_stock_set(amount_rows, threshold_count)

        for ts_code, _pct, _amount in rows:
            hits = 0
            if ts_code in pct_leaders:
                hits += 1
                stock_detail.setdefault(ts_code, {})["pct_leader"] = True
            if ts_code in amount_leaders:
                hits += 1
                stock_detail.setdefault(ts_code, {})["amount_leader"] = True
            if vol_ratio_by_stock.get(ts_code, 0.0) >= 1.5:
                hits += 1
                stock_detail.setdefault(ts_code, {})["volume_ratio_leader"] = True
            leadership_factor[ts_code] = 1.0 if hits >= 2 else 0.0
            stock_detail.setdefault(ts_code, {})["leadership_hits"] = hits

    return _SectorContext(
        leadership_factor=leadership_factor,
        stock_detail=stock_detail,
        vol_ratio_by_stock=vol_ratio_by_stock,
    )


def _top_count(size: int) -> int:
    """行业内前 30% 的数量，最少 1 只。"""
    return max(1, ceil(size * 0.3))


def _top_stock_set(rows: Sequence[tuple[str, float | None]], n: int) -> set[str]:
    """从 [(ts_code, value), ...] 取 value 最高的 N 只股票代码集合。"""
    valid = [(ts, value) for ts, value in rows if value is not None]
    valid.sort(key=lambda item: (-item[1], item[0]))
    return {ts for ts, _value in valid[:n]}


def _normalize_sector_score(raw_score: float) -> float:
    """原始分 0-70 归一化到 0-100。

    保持内部 bonus 表为整数（rank 0-50 + trend 0-20），输出层统一缩放，
    使得 sector 权重 0.10 的满贡献 = 10.0 分（与其他 filter 对齐）。
    """
    return max(0.0, min(100.0, raw_score / 70.0 * 100.0))
