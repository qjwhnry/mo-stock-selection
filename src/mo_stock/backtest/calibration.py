"""短线策略实证校准分析。

读 short_backtest_trade 表的回测样本，输出多维度统计：
- rule_score 分档（验证综合分排序有效性）
- 催化维度数分组（不含 exhaustion，验证多维共振）
- rank_in_day 分组（缓解 picked Top N 的选股偏差）
- sector_l1 分组（找强行业/弱行业）
- 维度命中组合分析（默认排除 exhaustion）
- exhaustion 质量分档（独立做）

所有函数默认统计 net_realized_return_pct（扣费后真实净收益），可切换 return_field。
所有函数为纯函数，输入 Iterable[Any] 只要含必要字段即可，方便单测用 SimpleNamespace。

催化维度的定义：limit / moneyflow / lhb / sector / theme（不含 exhaustion）。
exhaustion 是动量质量维度，作为风险修饰，不算入"多维共振"。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean
from typing import Any

CATALYST_DIMS = frozenset({"limit", "moneyflow", "lhb", "sector", "theme"})

_BUCKET_EDGES: list[tuple[float, float, str]] = [
    (0.0, 30.0, "[0,30)"),
    (30.0, 40.0, "[30,40)"),
    (40.0, 50.0, "[40,50)"),
    (50.0, 60.0, "[50,60)"),
    (60.0, 100.01, "[60,100]"),
]


@dataclass(frozen=True)
class BucketStats:
    """一档/一组的统计结果。"""
    label: str
    count: int
    avg_return: float
    median_return: float
    win_rate: float  # %


def _filter_rows(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str,
) -> list[tuple[Any, float]]:
    """筛选指定持有期且收益字段非空的样本。返回 (trade, return_pct) 元组列表。"""
    out: list[tuple[Any, float]] = []
    for t in trades:
        if getattr(t, "holding_days", None) != holding_days:
            continue
        val = getattr(t, return_field, None)
        if val is None:
            continue
        out.append((t, float(val)))
    return out


def _bucket_stats(label: str, returns: list[float]) -> BucketStats:
    if not returns:
        return BucketStats(label=label, count=0,
                            avg_return=0.0, median_return=0.0, win_rate=0.0)
    wins = sum(1 for r in returns if r > 0)
    sorted_r = sorted(returns)
    n = len(sorted_r)
    med = sorted_r[n // 2] if n % 2 == 1 \
          else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
    return BucketStats(
        label=label,
        count=n,
        avg_return=mean(returns),
        median_return=med,
        win_rate=wins / n * 100,
    )


def rule_score_buckets(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 rule_score 把交易分 5 档，统计每档的胜率和均收益。

    默认用 net_realized_return_pct（扣费后口径）。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    result: list[BucketStats] = []
    for low, high, label in _BUCKET_EDGES:
        bucket = [
            r for t, r in rows
            if low <= float(getattr(t, "rule_score", 0) or 0) < high
        ]
        result.append(_bucket_stats(label, bucket))
    return result
