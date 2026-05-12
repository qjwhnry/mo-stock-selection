"""历史涨停回调后的重启信号打分。

职责边界：
- `limit_filter` 处理当日涨停质量和 T-1 涨停后的断板反包。
- 本维度只处理最近一次涨停距今天 2-5 个交易日、今日未涨停的股票。
"""
from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.data_sources.calendar import recent_trading_days
from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage.models import DailyKline, LimitList


class LimitRestartFilter(FilterBase):
    """历史涨停后的回调质量 + 重启信号打分器。"""

    dim = "limit_restart"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        lookback_min = int(self.weights.get("lookback_min_days", 2))
        lookback_max = int(self.weights.get("lookback_max_days", 5))

        trading_days = recent_trading_days(session, trade_date, lookback_max + 22)
        if trade_date not in trading_days or len(trading_days) < lookback_max + 1:
            logger.warning("LimitRestartFilter: {} 交易日历不足，跳过", trade_date)
            return []

        day_index = {day: idx for idx, day in enumerate(trading_days)}
        today_idx = day_index[trade_date]
        recent_5_days = trading_days[max(0, today_idx - lookback_max):today_idx]
        scan_days = [*recent_5_days, trade_date]

        limit_rows = session.execute(
            select(LimitList.ts_code, LimitList.trade_date)
            .where(LimitList.limit_type == "U")
            .where(LimitList.trade_date.in_(scan_days))
        ).all()
        if not limit_rows:
            return []

        latest_limit: dict[str, date] = {}
        limit_count_5d: dict[str, int] = {}
        today_limit_codes: set[str] = set()
        for ts_code, limit_date in limit_rows:
            if limit_date == trade_date:
                today_limit_codes.add(ts_code)
            else:
                limit_count_5d[ts_code] = limit_count_5d.get(ts_code, 0) + 1
            old = latest_limit.get(ts_code)
            if old is None or day_index[limit_date] > day_index[old]:
                latest_limit[ts_code] = limit_date

        candidates: dict[str, tuple[date, int]] = {}
        for ts_code, limit_date in latest_limit.items():
            if ts_code in today_limit_codes:
                continue
            days_since_limit = today_idx - day_index[limit_date]
            if lookback_min <= days_since_limit <= lookback_max:
                candidates[ts_code] = (limit_date, days_since_limit)

        if not candidates:
            return []

        candidate_codes = set(candidates)
        kline_rows = session.execute(
            select(DailyKline)
            .where(DailyKline.ts_code.in_(candidate_codes))
            .where(DailyKline.trade_date.in_(trading_days))
        ).scalars().all()
        kline_map = {(row.ts_code, row.trade_date): row for row in kline_rows}

        results: list[ScoreResult] = []
        for ts_code, (limit_date, days_since_limit) in candidates.items():
            today = kline_map.get((ts_code, trade_date))
            limit_day = kline_map.get((ts_code, limit_date))
            if not _has_required_today_kline(today) or not _has_required_limit_kline(limit_day):
                continue

            assert today is not None
            assert limit_day is not None
            score, detail = self._score_one(
                ts_code=ts_code,
                trade_date=trade_date,
                limit_date=limit_date,
                days_since_limit=days_since_limit,
                limit_count_5d=limit_count_5d.get(ts_code, 0),
                trading_days=trading_days,
                day_index=day_index,
                kline_map=kline_map,
            )
            if score > 0:
                results.append(ScoreResult(ts_code, trade_date, self.dim, score, detail))

        logger.info("LimitRestartFilter: {} 产出 {} 只", trade_date, len(results))
        return results

    def _score_one(
        self,
        *,
        ts_code: str,
        trade_date: date,
        limit_date: date,
        days_since_limit: int,
        limit_count_5d: int,
        trading_days: list[date],
        day_index: dict[date, int],
        kline_map: dict[tuple[str, date], DailyKline],
    ) -> tuple[float, dict[str, Any]]:
        today = kline_map[(ts_code, trade_date)]
        limit_day = kline_map[(ts_code, limit_date)]

        detail: dict[str, Any] = {
            "limit_date": limit_date.isoformat(),
            "days_since_limit": days_since_limit,
            "limit_count_5d": limit_count_5d,
        }

        pullback_score = 0.0
        restart_score = 0.0
        penalty = 0.0

        volume_vs_limit = _safe_ratio(today.vol, limit_day.vol)
        detail["volume_vs_limit_day"] = _round_or_none(volume_vs_limit)
        volume_shrink_score = _volume_shrink_score(volume_vs_limit)
        detail["volume_shrink_score"] = volume_shrink_score
        pullback_score += volume_shrink_score

        min_low = _min_low_since_limit(
            ts_code, limit_date, trade_date, trading_days, day_index, kline_map,
        )
        detail["min_low_since_limit"] = _round_or_none(min_low)
        support_score, support_level = _support_score(
            min_low, limit_day.open, limit_day.close,
        )
        detail["support_score"] = support_score
        detail["support_level"] = support_level
        pullback_score += support_score

        day_score = _days_since_limit_score(days_since_limit)
        detail["days_since_limit_score"] = day_score
        pullback_score += day_score

        positive_score = _positive_candle_score(today.open, today.close, today.pre_close)
        detail["positive_candle_score"] = positive_score
        restart_score += positive_score

        prev5_vols = _prev_volumes(
            ts_code, trade_date, trading_days, day_index, kline_map, n=5,
        )
        volume_vs_prev5 = _safe_ratio(today.vol, fmean(prev5_vols) if prev5_vols else None)
        detail["volume_vs_prev5"] = _round_or_none(volume_vs_prev5)
        volume_restore_score = _volume_restore_score(volume_vs_prev5)
        detail["volume_restore_score"] = volume_restore_score
        restart_score += volume_restore_score

        close_position_score = _close_position_score(today.high, today.low, today.close)
        detail["close_position_score"] = close_position_score
        restart_score += close_position_score

        low_open_score = _low_open_high_close_score(today.open, today.close, today.pre_close)
        detail["low_open_high_close_score"] = low_open_score
        restart_score += low_open_score

        prev20_vols = _prev_volumes(
            ts_code, limit_date, trading_days, day_index, kline_map, n=20,
        )
        min_prev20_days = int(self.weights.get("min_prev20_volume_days", 10))
        abnormal_ratio = float(self.weights.get("abnormal_limit_volume_ratio", 3.0))
        if len(prev20_vols) < min_prev20_days:
            detail["insufficient_prev20_volume"] = True
            detail["limit_day_volume_vs_prev20"] = None
        else:
            limit_vs_prev20 = _safe_ratio(limit_day.vol, fmean(prev20_vols))
            detail["limit_day_volume_vs_prev20"] = _round_or_none(limit_vs_prev20)
            if limit_vs_prev20 is not None and limit_vs_prev20 > abnormal_ratio:
                detail["abnormal_limit_volume_penalty"] = 20
                penalty += 20

        per_extra_limit_penalty = int(self.weights.get("multi_limit_penalty_per_extra", 15))
        multi_limit_penalty = max(limit_count_5d - 1, 0) * per_extra_limit_penalty
        if multi_limit_penalty:
            detail["multi_limit_penalty"] = multi_limit_penalty
            penalty += multi_limit_penalty

        detail["pullback_score"] = pullback_score
        detail["restart_score"] = restart_score
        detail["penalty"] = penalty

        # 最小重启门槛：至少命中一个核心重启信号（收阳/重心上移/低开高走/量能恢复）
        min_restart = int(self.weights.get("min_restart_score", 15))
        if restart_score < min_restart:
            detail["restart_below_min"] = True
            return 0.0, detail

        return clamp(pullback_score + restart_score - penalty), detail


def _has_required_today_kline(row: DailyKline | None) -> bool:
    if row is None:
        return False
    return all(
        value is not None
        for value in (row.open, row.high, row.low, row.close, row.pre_close, row.vol)
    )


def _has_required_limit_kline(row: DailyKline | None) -> bool:
    if row is None:
        return False
    return all(value is not None for value in (row.open, row.close, row.vol))


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _volume_shrink_score(ratio: float | None) -> int:
    if ratio is None:
        return 0
    if ratio <= 0.3:
        return 20
    if ratio <= 0.5:
        return 15
    if ratio <= 0.7:
        return 8
    return 0


def _support_score(
    min_low_since_limit: float | None,
    limit_open: float | None,
    limit_close: float | None,
) -> tuple[int, str]:
    if min_low_since_limit is None or limit_open is None or limit_close is None:
        return 0, "unknown"
    low_bound = min(limit_open, limit_close)
    high_bound = max(limit_open, limit_close)
    if min_low_since_limit >= high_bound:
        return 15, "full"
    if min_low_since_limit >= low_bound:
        return 8, "partial"
    return 0, "broken"


def _days_since_limit_score(days_since_limit: int) -> int:
    return {2: 10, 3: 8, 4: 5, 5: 3}.get(days_since_limit, 0)


def _positive_candle_score(
    open_: float | None,
    close: float | None,
    pre_close: float | None,
) -> int:
    if open_ is None or close is None:
        return 0
    if close > open_:
        return 15
    if pre_close is not None and close > pre_close:
        return 8
    return 0


def _volume_restore_score(ratio: float | None) -> int:
    if ratio is None:
        return 0
    if ratio >= 1.5:
        return 15
    if ratio >= 1.0:
        return 10
    if ratio >= 0.7:
        return 5
    return 0


def _close_position_score(
    high: float | None,
    low: float | None,
    close: float | None,
) -> int:
    if high is None or low is None or close is None or high <= low:
        return 0
    position = (close - low) / (high - low)
    if position >= 2 / 3:
        return 15
    if position >= 1 / 3:
        return 8
    return 0


def _low_open_high_close_score(
    open_: float | None,
    close: float | None,
    pre_close: float | None,
) -> int:
    if open_ is None or close is None or pre_close is None:
        return 0
    return 10 if open_ < pre_close and close > open_ else 0


def _min_low_since_limit(
    ts_code: str,
    limit_date: date,
    trade_date: date,
    trading_days: list[date],
    day_index: dict[date, int],
    kline_map: dict[tuple[str, date], DailyKline],
) -> float | None:
    lows: list[float] = []
    start = day_index[limit_date] + 1
    end = day_index[trade_date]
    for day in trading_days[start:end + 1]:
        row = kline_map.get((ts_code, day))
        if row is not None and row.low is not None:
            lows.append(row.low)
    return min(lows) if lows else None


def _prev_volumes(
    ts_code: str,
    anchor_date: date,
    trading_days: list[date],
    day_index: dict[date, int],
    kline_map: dict[tuple[str, date], DailyKline],
    *,
    n: int,
) -> list[float]:
    end = day_index[anchor_date]
    start = max(0, end - n)
    vols: list[float] = []
    for day in trading_days[start:end]:
        row = kline_map.get((ts_code, day))
        if row is not None and row.vol is not None and row.vol > 0:
            vols.append(row.vol)
    return vols
