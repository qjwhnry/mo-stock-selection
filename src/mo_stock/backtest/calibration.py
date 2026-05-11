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


def _count_catalyst_hits(dim_detail: dict | None) -> int:
    """从 dim_detail 数命中的催化维度数（score > 0，不含 exhaustion）。"""
    if not dim_detail:
        return 0
    return sum(
        1 for d, payload in dim_detail.items()
        if d in CATALYST_DIMS
        and isinstance(payload, dict)
        and float(payload.get("score") or 0) > 0
    )


def catalyst_dims_groups(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> dict[str, BucketStats]:
    """按催化维度数（1/2/3/4+）分组，验证多维共振假设。

    催化维度 = limit/moneyflow/lhb/sector/theme，不含 exhaustion。
    从 dim_detail 数 score > 0 的命中维度（active_dims 字段把 exhaustion 也算进去了，
    会污染共振判读）。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    groups: dict[str, list[float]] = {"1": [], "2": [], "3": [], "4+": []}
    for t, r in rows:
        n = _count_catalyst_hits(getattr(t, "dim_detail", None))
        if n <= 0:
            continue
        key = str(n) if n < 4 else "4+"
        groups[key].append(r)

    return {key: _bucket_stats(key, bucket) for key, bucket in groups.items()}


def rank_in_day_groups(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> dict[str, BucketStats]:
    """按 rank_in_day 分 3 段（1-5 / 6-10 / 11-20）统计。

    缓解 picked Top N 选股偏差——同一日内排名是绝对可比的，
    不依赖"低 rule_score 是否在弱市才进 picked"的混淆。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    edges = [(1, 5, "1-5"), (6, 10, "6-10"), (11, 20, "11-20")]
    groups: dict[str, list[float]] = {label: [] for _, _, label in edges}
    for t, r in rows:
        rank = int(getattr(t, "rank_in_day", 0) or 0)
        for low, high, label in edges:
            if low <= rank <= high:
                groups[label].append(r)
                break

    return {label: _bucket_stats(label, bucket) for label, bucket in groups.items()}


def sector_groups(
    trades: Iterable[Any],
    holding_days: int,
    min_count: int = 20,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 sector_l1 分组，过滤样本数 < min_count 的行业，按 avg_return 降序返回。

    sector_l1 来自 short_backtest_trade 的快照列（信号日时点），
    不依赖 index_member 的当前状态，可跨时间稳定比较。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    by_sector: dict[str, list[float]] = {}
    for t, r in rows:
        sec = getattr(t, "sector_l1", None)
        if not sec:
            continue
        by_sector.setdefault(sec, []).append(r)

    result = [
        _bucket_stats(sec, bucket)
        for sec, bucket in by_sector.items()
        if len(bucket) >= min_count
    ]
    result.sort(key=lambda b: b.avg_return, reverse=True)
    return result


def dim_combination_analysis(
    trades: Iterable[Any],
    holding_days: int,
    min_count: int = 5,
    exclude_dims: frozenset[str] = frozenset({"exhaustion"}),
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 dim_detail 命中维度集合分组（默认排除 exhaustion），返回样本数 ≥ min_count 的组合。

    label 形如 "limit+lhb"、"moneyflow+sector+theme"，键名按字母序保证稳定。
    按 avg_return 降序返回。

    exhaustion 是动量质量维度（不是催化），混进组合 label 会让"limit+exhaustion"看起来像"双信号共振"。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    by_combo: dict[str, list[float]] = {}
    for t, r in rows:
        detail = getattr(t, "dim_detail", None) or {}
        hit_dims = sorted(
            d for d, payload in detail.items()
            if d not in exclude_dims
            and isinstance(payload, dict)
            and float(payload.get("score") or 0) > 0
        )
        if not hit_dims:
            continue
        key = "+".join(hit_dims)
        by_combo.setdefault(key, []).append(r)

    result = [
        _bucket_stats(key, bucket)
        for key, bucket in by_combo.items()
        if len(bucket) >= min_count
    ]
    result.sort(key=lambda b: b.avg_return, reverse=True)
    return result


def exhaustion_quality_buckets(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 exhaustion 分数分 3 档：差(<50) / 中(50-80) / 健康(>=80)。

    回答"动量质量是否真的影响后续收益"——若健康档显著好于差档，
    说明 exhaustion 维度有判别力。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    edges = [(0.0, 50.0, "差(<50)"),
             (50.0, 80.0, "中(50-80)"),
             (80.0, 100.01, "健康(>=80)")]
    groups: list[tuple[str, list[float]]] = [(label, []) for _, _, label in edges]
    for t, r in rows:
        detail = getattr(t, "dim_detail", None) or {}
        ex_score = float((detail.get("exhaustion") or {}).get("score") or 0)
        for i, (low, high, label) in enumerate(edges):
            if low <= ex_score < high:
                groups[i][1].append(r)
                break

    return [_bucket_stats(label, bucket) for label, bucket in groups]
