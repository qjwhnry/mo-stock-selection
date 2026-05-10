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
