"""短线策略校准分析的单元测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _make_trade(
    rule_score: float = 50.0,
    net_realized_return_pct: float | None = 1.0,
    holding_days: int = 1,
    active_dims: int = 2,
    rank_in_day: int = 1,
    dim_detail: dict | None = None,
    sector_l1: str | None = "电子",
):
    """构造测试用 trade 行（兼容 ShortBacktestTrade ORM 字段）。"""
    return SimpleNamespace(
        rule_score=rule_score,
        net_realized_return_pct=net_realized_return_pct,
        realized_return_pct=net_realized_return_pct,  # 测试便利
        holding_days=holding_days,
        active_dims=active_dims,
        rank_in_day=rank_in_day,
        dim_detail=dim_detail or {},
        sector_l1=sector_l1,
        exit_date=None,
        stop_hit=False,
    )


def test_rule_score_buckets_groups_correctly() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trades = [
        _make_trade(rule_score=15, net_realized_return_pct=2.0),
        _make_trade(rule_score=35, net_realized_return_pct=1.5),
        _make_trade(rule_score=42, net_realized_return_pct=-1.0),
        _make_trade(rule_score=55, net_realized_return_pct=3.0),
        _make_trade(rule_score=72, net_realized_return_pct=4.5),
    ]
    buckets = rule_score_buckets(trades, holding_days=1)

    assert len(buckets) == 5
    labels = [b.label for b in buckets]
    assert labels == ["[0,30)", "[30,40)", "[40,50)", "[50,60)", "[60,100]"]
    assert buckets[0].count == 1
    assert buckets[0].avg_return == pytest.approx(2.0)
    assert buckets[0].win_rate == pytest.approx(100.0)


def test_rule_score_buckets_filters_by_holding_days() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trades = [
        _make_trade(rule_score=50, net_realized_return_pct=1.0, holding_days=1),
        _make_trade(rule_score=50, net_realized_return_pct=2.0, holding_days=3),
    ]
    buckets_1d = rule_score_buckets(trades, holding_days=1)
    buckets_3d = rule_score_buckets(trades, holding_days=3)
    assert buckets_1d[3].avg_return == pytest.approx(1.0)
    assert buckets_3d[3].avg_return == pytest.approx(2.0)


def test_rule_score_buckets_uses_net_return_by_default() -> None:
    """默认应该用 net_realized_return_pct（扣费后），不是 realized_return_pct。"""
    from mo_stock.backtest.calibration import rule_score_buckets

    trade = SimpleNamespace(
        rule_score=50, holding_days=1, active_dims=2, rank_in_day=1,
        dim_detail={}, sector_l1="电子", exit_date=None, stop_hit=False,
        realized_return_pct=5.0,        # 扣费前
        net_realized_return_pct=4.5,    # 扣费后
    )
    buckets = rule_score_buckets([trade], holding_days=1)
    # 默认取 net_realized_return_pct
    assert buckets[3].avg_return == pytest.approx(4.5)


def test_rule_score_buckets_can_switch_return_field() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trade = SimpleNamespace(
        rule_score=50, holding_days=1, active_dims=2, rank_in_day=1,
        dim_detail={}, sector_l1="电子", exit_date=None, stop_hit=False,
        realized_return_pct=5.0,
        net_realized_return_pct=4.5,
    )
    buckets = rule_score_buckets([trade], holding_days=1,
                                  return_field="realized_return_pct")
    assert buckets[3].avg_return == pytest.approx(5.0)


def test_catalyst_dims_groups_excludes_exhaustion() -> None:
    from mo_stock.backtest.calibration import catalyst_dims_groups

    trades = [
        # 1 个催化（limit），还有 exhaustion 不算
        _make_trade(net_realized_return_pct=1.0, dim_detail={
            "limit": {"score": 50}, "exhaustion": {"score": 80},
        }),
        # 2 个催化（limit + moneyflow）
        _make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
        }),
        # 3 个催化
        _make_trade(net_realized_return_pct=3.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
            "lhb": {"score": 40},
        }),
        # 5 个催化全 → 4+ 组
        _make_trade(net_realized_return_pct=4.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
            "lhb": {"score": 40}, "sector": {"score": 60},
            "theme": {"score": 50},
        }),
    ]
    groups = catalyst_dims_groups(trades, holding_days=1)
    assert set(groups.keys()) == {"1", "2", "3", "4+"}
    assert groups["1"].count == 1
    assert groups["1"].avg_return == pytest.approx(1.0)
    assert groups["4+"].count == 1
    assert groups["4+"].avg_return == pytest.approx(4.0)


def test_catalyst_dims_groups_zero_score_not_counted() -> None:
    from mo_stock.backtest.calibration import catalyst_dims_groups

    trade = _make_trade(net_realized_return_pct=1.0, dim_detail={
        "limit": {"score": 50},
        "moneyflow": {"score": 0},  # score=0 不算命中
        "exhaustion": {"score": 90},  # 不算
    })
    groups = catalyst_dims_groups([trade], holding_days=1)
    # 只有 limit 命中 → 1 组
    assert groups["1"].count == 1
    assert groups["2"].count == 0


def test_rank_in_day_groups_returns_three_buckets() -> None:
    from mo_stock.backtest.calibration import rank_in_day_groups

    trades = [
        _make_trade(rank_in_day=1, net_realized_return_pct=3.0),
        _make_trade(rank_in_day=3, net_realized_return_pct=2.0),
        _make_trade(rank_in_day=5, net_realized_return_pct=1.5),
        _make_trade(rank_in_day=8, net_realized_return_pct=1.0),
        _make_trade(rank_in_day=15, net_realized_return_pct=0.0),
        _make_trade(rank_in_day=20, net_realized_return_pct=-0.5),
    ]
    groups = rank_in_day_groups(trades, holding_days=1)
    assert set(groups.keys()) == {"1-5", "6-10", "11-20"}
    assert groups["1-5"].count == 3
    assert groups["1-5"].avg_return == pytest.approx((3 + 2 + 1.5) / 3)
    assert groups["6-10"].count == 1
    assert groups["11-20"].count == 2


def test_sector_groups_filters_by_min_count_and_sorts() -> None:
    from mo_stock.backtest.calibration import sector_groups

    trades = (
        # 电子：3 条，均收益 2.0
        [_make_trade(sector_l1="电子", net_realized_return_pct=2.0)] * 3
        # 计算机：3 条，均收益 -0.5
        + [_make_trade(sector_l1="计算机", net_realized_return_pct=-0.5)] * 3
        # 食品饮料：1 条，应被 min_count 过滤
        + [_make_trade(sector_l1="食品饮料", net_realized_return_pct=5.0)]
    )
    result = sector_groups(trades, holding_days=1, min_count=3)
    labels = [b.label for b in result]
    # 应按 avg_return 降序，且不含 sample 不足的食品饮料
    assert labels == ["电子", "计算机"]
    assert "食品饮料" not in labels
    assert result[0].avg_return == pytest.approx(2.0)


def test_sector_groups_skips_none_sector() -> None:
    from mo_stock.backtest.calibration import sector_groups

    trades = [_make_trade(sector_l1=None, net_realized_return_pct=1.0)] * 5
    result = sector_groups(trades, holding_days=1, min_count=3)
    # sector_l1=None 应被跳过，不要把 None 当成一个分组
    assert result == []


def test_dim_combination_analysis_excludes_exhaustion_by_default() -> None:
    from mo_stock.backtest.calibration import dim_combination_analysis

    trades = (
        # 组合 limit+lhb，3 笔
        [_make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40},
            "exhaustion": {"score": 95},  # 应被排除，不影响组合 label
        })] * 3
    )
    combos = dim_combination_analysis(trades, holding_days=1, min_count=3)
    assert len(combos) == 1
    assert combos[0].label == "lhb+limit"  # 不应是 "exhaustion+lhb+limit"
    assert combos[0].count == 3


def test_dim_combination_analysis_filters_min_count_and_sorts() -> None:
    from mo_stock.backtest.calibration import dim_combination_analysis

    trades = (
        # 组合 A：3 笔，均 2.5
        [_make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        + [_make_trade(net_realized_return_pct=3.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        + [_make_trade(net_realized_return_pct=2.5, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        # 组合 B：2 笔（< min_count 应被过滤）
        + [_make_trade(net_realized_return_pct=4.0, dim_detail={
            "moneyflow": {"score": 50}, "sector": {"score": 50}})] * 2
    )
    combos = dim_combination_analysis(trades, holding_days=1, min_count=3)
    assert len(combos) == 1
    assert combos[0].label == "lhb+limit"


def test_exhaustion_quality_buckets_groups_by_score() -> None:
    from mo_stock.backtest.calibration import exhaustion_quality_buckets

    trades = [
        _make_trade(net_realized_return_pct=-1.0, dim_detail={
            "exhaustion": {"score": 30}}),  # 差
        _make_trade(net_realized_return_pct=1.0, dim_detail={
            "exhaustion": {"score": 60}}),  # 中
        _make_trade(net_realized_return_pct=2.0, dim_detail={
            "exhaustion": {"score": 85}}),  # 健康
        _make_trade(net_realized_return_pct=3.0, dim_detail={
            "exhaustion": {"score": 95}}),  # 健康
    ]
    buckets = exhaustion_quality_buckets(trades, holding_days=1)
    labels = [b.label for b in buckets]
    assert labels == ["差(<50)", "中(50-80)", "健康(>=80)"]
    assert buckets[0].count == 1
    assert buckets[2].count == 2
    assert buckets[2].avg_return == pytest.approx(2.5)
