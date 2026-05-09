"""短期动量质量检测维度（替代 sentiment 权重位）。

**核心思路**：
短线策略的 5 个维度全是"正向信号"（最近有好事发生吗？），
没有人问"动量还健康吗？"。
ExhaustionFilter 填补这个盲区：检测冲高回落、量价背离、动量衰减等"动量质量"信号，
按 0-100 新鲜度打分，质量越差分越低。

**信号设计**（详见 config/weights.yaml 的 exhaustion_filter 节）：
1. 5 日累计涨幅（短线追势策略中已禁用，max=0；极端透支由 hard_reject 处理）
2. MA5 偏离度（同上，已禁用）
3. 冲高回落（长上影 + 收盘弱 + 近期上涨 + 当天冲高，最高扣 15）
4. 量价背离（缩量上涨 / 放量滞涨，最高扣 20）
5. 连涨天数（散户追涨风险，最高扣 10）
6. 动量衰减（涨幅集中在前半段，最高扣 10）

得分 = clamp(100 - Σpenalties, 0, 100)。所有活跃 A 股都会产出分数，
正常股的分数接近 100，动量质量差的股被显著拉低。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline


class ExhaustionFilter(FilterBase):
    """短期动量质量/新鲜度打分器。"""

    dim = "exhaustion"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        cfg = self.weights

        # 获取最近 N 个交易日（用于计算 MA5 + T-5 累计收益）
        trade_dates = repo.get_recent_trade_dates(session, trade_date, 11)
        if len(trade_dates) < 6:
            logger.warning("ExhaustionFilter: 交易日不足 6 日，跳过")
            return []

        # 批量读取多日 K 线
        rows = session.execute(
            select(
                DailyKline.ts_code,
                DailyKline.trade_date,
                DailyKline.close,
                DailyKline.open,
                DailyKline.high,
                DailyKline.low,
                DailyKline.vol,
            )
            .where(DailyKline.trade_date.in_(trade_dates))
        ).all()

        # 按股票分组，组内按日期排序
        kline_by_stock: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.close is None:
                continue
            kline_by_stock.setdefault(row.ts_code, []).append({
                "trade_date": row.trade_date,
                "close": row.close,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "vol": row.vol,
            })

        for ts_code in kline_by_stock:
            kline_by_stock[ts_code].sort(key=lambda k: k["trade_date"])

        results: list[ScoreResult] = []
        for ts_code, klines in kline_by_stock.items():
            if len(klines) < 6:
                continue

            # 当日 K 线（最后一条），必须匹配 trade_date
            today = klines[-1]
            if today["trade_date"] != trade_date:
                continue

            score = 100.0
            detail: dict[str, Any] = {}

            close_today = today["close"]

            # ---------- 1. 5 日累计涨幅 ----------
            penalty_5d = _penalty_5d_return(klines, cfg)
            if penalty_5d > 0:
                score -= penalty_5d
                detail["penalty_5d_return"] = round(penalty_5d, 1)

            # ---------- 2. MA5 偏离度 ----------
            # 与 5 日涨幅高度共线：当 5d 惩罚已触发时，MA5 偏离惩罚打 5 折，
            # 避免两个信号对同一事实（"价格涨了太多"）重复惩罚。
            penalty_ma = _penalty_ma5_deviation(klines, cfg)
            if penalty_ma > 0:
                if penalty_5d > 0:
                    penalty_ma *= 0.5
                    detail["ma5_penalty_discounted"] = True
                score -= penalty_ma
                detail["penalty_ma5_deviation"] = round(penalty_ma, 1)

            # ---------- 3. 冲高回落 ----------
            penalty_shadow = _penalty_upper_shadow(klines, today, cfg)
            if penalty_shadow > 0:
                score -= penalty_shadow
                detail["penalty_upper_shadow"] = round(penalty_shadow, 1)
                shadow_metrics = _compute_upper_shadow_metrics(klines, today)
                if shadow_metrics is not None:
                    detail["upper_shadow_ratio"] = round(
                        shadow_metrics["upper_shadow_ratio"], 2,
                    )
                    detail["close_position"] = round(
                        shadow_metrics["close_position"], 2,
                    )
                    detail["high_return_from_pre_close"] = round(
                        shadow_metrics["high_return_from_pre_close"], 2,
                    )

            intraday_amplitude = _compute_intraday_amplitude_pct(today)
            if intraday_amplitude is not None:
                detail["intraday_amplitude_pct"] = round(intraday_amplitude, 2)

            # ---------- 4. 量价背离 ----------
            penalty_vol = _penalty_volume_divergence(klines, today, cfg)
            if penalty_vol > 0:
                score -= penalty_vol
                detail["penalty_volume_divergence"] = round(penalty_vol, 1)

            # ---------- 5. 连涨天数 ----------
            penalty_consec = _penalty_consecutive_up(klines, cfg)
            if penalty_consec > 0:
                score -= penalty_consec
                detail["penalty_consecutive_up"] = round(penalty_consec, 1)

            # ---------- 6. 动量衰减 ----------
            penalty_decay = _penalty_momentum_decay(klines, cfg)
            if penalty_decay > 0:
                score -= penalty_decay
                detail["penalty_momentum_decay"] = round(penalty_decay, 1)

            final = clamp(score)
            detail["freshness_score"] = round(final, 1)

            # 记录关键原始数据供 AI / 报告使用
            detail["close"] = close_today
            if len(klines) >= 6 and klines[-6]["close"] is not None:
                close_5d = klines[-6]["close"]
                ret_5d = (close_today - close_5d) / close_5d * 100
                detail["ret_5d_pct"] = round(ret_5d, 2)

            ma5 = _compute_ma5(klines)
            if ma5 is not None and ma5 > 0:
                detail["ma5"] = round(ma5, 2)
                detail["ma5_deviation_pct"] = round(
                    (close_today - ma5) / ma5 * 100, 2,
                )

            detail["consecutive_up_days"] = _count_consecutive_up(klines)

            results.append(ScoreResult(
                ts_code=ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=final,
                detail=detail,
            ))

        logger.info(
            "ExhaustionFilter: {} 共 {} 只股票出分，"
            "均分 {:.1f}，≤50 分（动量质量差）{} 只",
            trade_date,
            len(results),
            sum(r.score for r in results) / max(len(results), 1),
            sum(1 for r in results if r.score <= 50),
        )
        return results


# ---------------------------------------------------------------------------
# 各信号惩罚函数（纯函数，方便测试）
# ---------------------------------------------------------------------------


def _compute_ma5(klines: list[dict[str, Any]]) -> float | None:
    """计算最近 5 日简单移动平均（MA5）。"""
    if len(klines) < 5:
        return None
    closes: list[float] = [
        float(k["close"]) for k in klines[-5:]
        if k.get("close") is not None
    ]
    if len(closes) < 5:
        return None
    return sum(closes) / len(closes)


def _compute_intraday_amplitude_pct(today: dict[str, Any]) -> float | None:
    """计算当日振幅百分比；字段缺失或 close 无效时返回 None。"""
    high_raw = today.get("high")
    low_raw = today.get("low")
    close_raw = today.get("close")
    if high_raw is None or low_raw is None or close_raw is None:
        return None
    high = float(high_raw)
    low = float(low_raw)
    close = float(close_raw)
    if close <= 0:
        return None
    return (high - low) / close * 100


def _compute_upper_shadow_metrics(
    klines: list[dict[str, Any]],
    today: dict[str, Any],
) -> dict[str, float] | None:
    """计算冲高回落相关指标；任一关键字段无效时返回 None。"""
    if len(klines) < 4:
        return None

    open_raw = today.get("open")
    high_raw = today.get("high")
    low_raw = today.get("low")
    close_raw = today.get("close")
    pre_close_raw = klines[-2].get("close") if len(klines) >= 2 else None
    close_3d_ago_raw = klines[-4].get("close")
    if (
        open_raw is None
        or high_raw is None
        or low_raw is None
        or close_raw is None
        or pre_close_raw is None
        or close_3d_ago_raw is None
    ):
        return None

    open_price = float(open_raw)
    high = float(high_raw)
    low = float(low_raw)
    close = float(close_raw)
    pre_close = float(pre_close_raw)
    close_3d_ago = float(close_3d_ago_raw)
    amplitude = high - low
    if amplitude <= 0 or pre_close <= 0 or close_3d_ago <= 0:
        return None

    return {
        "upper_shadow_ratio": (high - max(open_price, close)) / amplitude,
        "close_position": (close - low) / amplitude,
        "ret_3d": (close - close_3d_ago) / close_3d_ago * 100,
        "high_return_from_pre_close": (high - pre_close) / pre_close * 100,
    }


def _penalty_5d_return(
    klines: list[dict[str, Any]], cfg: dict[str, Any],
) -> float:
    """5 日累计涨幅惩罚。

    阈值从 cfg 读取，≤low 不罚，low~high 线性 0~max_penalty，≥high 满罚。
    当前短线配置 penalty_max=0（已禁用），max=0 时直接返回。
    """
    max_penalty = float(cfg.get("penalty_5d_return_max", 35))
    if max_penalty <= 0:
        return 0
    if len(klines) < 6:
        return 0
    close_today = float(klines[-1]["close"])
    close_5d_ago = float(klines[-6]["close"])
    if close_5d_ago <= 0:
        return 0
    ret_5d = (close_today - close_5d_ago) / close_5d_ago * 100
    low = float(cfg.get("threshold_5d_return_low", 5.0))
    high = float(cfg.get("threshold_5d_return_high", 25.0))
    if ret_5d <= low:
        return 0
    if ret_5d >= high:
        return max_penalty
    return (ret_5d - low) / (high - low) * max_penalty


def _penalty_ma5_deviation(
    klines: list[dict[str, Any]], cfg: dict[str, Any],
) -> float:
    """MA5 偏离度惩罚。

    阈值从 cfg 读取，≤low 不罚，low~high 线性 0~max_penalty，≥high 满罚。
    当前短线配置 penalty_max=0（已禁用），max=0 时直接返回。
    """
    max_penalty = float(cfg.get("penalty_ma5_deviation_max", 25))
    if max_penalty <= 0:
        return 0
    ma5 = _compute_ma5(klines)
    if ma5 is None or ma5 <= 0:
        return 0
    close_today = float(klines[-1]["close"])
    deviation = (close_today - ma5) / ma5 * 100
    low = float(cfg.get("threshold_ma5_deviation_low", 2.0))
    high = float(cfg.get("threshold_ma5_deviation_high", 8.0))
    if deviation <= low:
        return 0
    if deviation >= high:
        return max_penalty
    return (deviation - low) / (high - low) * max_penalty


def _penalty_upper_shadow(
    klines: list[dict[str, Any]],
    today: dict[str, Any],
    cfg: dict[str, Any],
) -> float:
    """冲高回落惩罚。

    四个条件同时满足才触发：
    1. 上影线占比高；
    2. 收盘在全日振幅下半区；
    3. 近 3 日已有一定涨幅；
    4. 今日 high 相对昨收确实有明显冲高。

    触发后使用 upper_shadow_ratio 直接作为严重度，刚过阈值即有一定扣分。
    这是有意的保守设计：信号权重较低，用于排序降级而非一票否决。
    """
    max_penalty = float(cfg.get("penalty_upper_shadow_max", 15))
    if max_penalty <= 0:
        return 0

    metrics = _compute_upper_shadow_metrics(klines, today)
    if metrics is None:
        return 0

    upper_shadow_ratio = metrics["upper_shadow_ratio"]
    close_position = metrics["close_position"]
    ret_3d = metrics["ret_3d"]
    high_return = metrics["high_return_from_pre_close"]

    threshold_shadow = float(cfg.get("threshold_upper_shadow_ratio", 0.6))
    threshold_close = float(cfg.get("threshold_close_position", 0.5))
    threshold_ret_3d = float(cfg.get("threshold_upper_shadow_3d_return", 5.0))
    threshold_high_return = float(
        cfg.get("threshold_high_return_from_pre_close", 3.0),
    )
    if not (
        upper_shadow_ratio > threshold_shadow
        and close_position < threshold_close
        and ret_3d > threshold_ret_3d
        and high_return >= threshold_high_return
    ):
        return 0

    close_penalty_factor = 1.0 + (threshold_close - close_position) * 0.5
    penalty = upper_shadow_ratio * max_penalty * close_penalty_factor
    return min(max_penalty, max(0.0, penalty))


def _penalty_volume_divergence(
    klines: list[dict[str, Any]],
    today: dict[str, Any],
    cfg: dict[str, Any],
) -> float:
    """量价背离惩罚。

    两种模式：
    - 缩量上涨：近 3 日涨幅 > 5%，但今日量 < 近 5 日均量的 60%
    - 放量滞涨：今日量 > 5 日均量的 2 倍，但相对昨收涨幅 < 2%
    取两者中的较大惩罚。
    """
    max_penalty = float(cfg.get("penalty_volume_divergence_max", 20))
    shrink_vol = float(cfg.get("threshold_shrink_vol_ratio", 0.6))
    shrink_3d = float(cfg.get("threshold_shrink_3d_return", 5.0))
    blowoff_vol = float(cfg.get("threshold_blowoff_vol_ratio", 2.0))
    blowoff_pct = float(cfg.get("threshold_blowoff_pct_chg", 2.0))

    # 计算近 5 日均量
    vols: list[float] = [
        float(v) for k in klines[-6:-1]
        if (v := k.get("vol")) is not None
    ]
    if len(vols) < 5:
        return 0
    avg_vol_5d = sum(vols) / len(vols)
    today_vol_raw = today.get("vol")
    if today_vol_raw is None or avg_vol_5d <= 0:
        return 0
    today_vol = float(today_vol_raw)

    penalty_shrink = 0.0
    penalty_blowoff = 0.0

    # 缩量上涨检测
    if len(klines) >= 4:
        close_3d_ago_raw = klines[-4].get("close")
        if close_3d_ago_raw is not None and float(close_3d_ago_raw) > 0:
            close_3d_ago = float(close_3d_ago_raw)
            close_today = float(today["close"])
            ret_3d = (close_today - close_3d_ago) / close_3d_ago * 100
            if ret_3d > shrink_3d and today_vol < avg_vol_5d * shrink_vol:
                ratio = today_vol / avg_vol_5d
                severity = (shrink_vol - ratio) / shrink_vol
                penalty_shrink = severity * max_penalty

    # 放量滞涨检测
    # 使用 pct_chg（相对昨收）：放量但未能有效收复昨收，视为滞涨/弱反抽风险。
    if len(klines) >= 2:
        pre_close = klines[-2].get("close")
        if pre_close is not None and float(pre_close) > 0:
            close_today = float(today["close"])
            pct_chg = (close_today - float(pre_close)) / float(pre_close) * 100
            if today_vol > avg_vol_5d * blowoff_vol and pct_chg < blowoff_pct:
                penalty_blowoff = max_penalty * 0.8

    return min(max_penalty, max(penalty_shrink, penalty_blowoff))


def _count_consecutive_up(klines: list[dict[str, Any]]) -> int:
    """从最新一天往回数，连续上涨的天数。

    上涨判定：close > open（收阳线）。
    """
    count = 0
    for k in reversed(klines):
        if k.get("close") is not None and k.get("open") is not None:
            if k["close"] > k["open"]:
                count += 1
            else:
                break
    return count


def _penalty_consecutive_up(
    klines: list[dict[str, Any]], cfg: dict[str, Any],
) -> float:
    """连涨天数惩罚。

    阈值从 cfg 读取：low ~ mid 轻罚 ratio_low；mid ~ high 中罚 ratio_mid；≥ high 满罚。
    """
    consecutive = _count_consecutive_up(klines)
    max_penalty = float(cfg.get("penalty_consecutive_up_max", 10))
    threshold_high = int(cfg.get("threshold_consecutive_up_high", 7))
    threshold_mid = int(cfg.get("threshold_consecutive_up_mid", 5))
    threshold_low = int(cfg.get("threshold_consecutive_up_low", 3))
    ratio_mid = float(cfg.get("ratio_consecutive_up_mid", 0.7))
    ratio_low = float(cfg.get("ratio_consecutive_up_low", 0.35))
    if consecutive >= threshold_high:
        return max_penalty
    if consecutive >= threshold_mid:
        return max_penalty * ratio_mid
    if consecutive >= threshold_low:
        return max_penalty * ratio_low
    return 0


def _penalty_momentum_decay(
    klines: list[dict[str, Any]], cfg: dict[str, Any],
) -> float:
    """动量衰减惩罚。

    条件：5 日涨幅 > 10%，但最近 2 日涨幅占 5 日总涨幅的比例 < 20%。
    意味着大部分涨幅发生在 3-5 天前，近期动量已耗尽。
    """
    if len(klines) < 6:
        return 0
    close_today = float(klines[-1]["close"])
    close_5d = float(klines[-6]["close"])
    if close_5d <= 0:
        return 0
    ret_5d = (close_today - close_5d) / close_5d * 100
    threshold_5d = float(cfg.get("threshold_decay_5d_return", 10.0))
    if ret_5d <= threshold_5d:
        return 0

    close_2d = float(klines[-3]["close"])
    if close_2d <= 0:
        return 0
    ret_2d = (close_today - close_2d) / close_2d * 100

    threshold_2d_ratio = float(cfg.get("threshold_decay_2d_ratio", 0.2))
    if ret_2d < ret_5d * threshold_2d_ratio:
        max_penalty = float(cfg.get("penalty_momentum_decay_max", 10))
        return max_penalty
    return 0
