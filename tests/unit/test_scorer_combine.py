"""scorer.combine 的权重计算逻辑测试。

测核心 _weighted_combine 纯函数；完整端到端（带 DB 和硬规则）放在 integration 测试。
"""
from __future__ import annotations

import pytest

from mo_stock.filters.base import clamp
from mo_stock.scorer.combine import _weighted_combine


class TestClamp:
    def test_within_range(self) -> None:
        assert clamp(50) == 50

    def test_above_max(self) -> None:
        assert clamp(150) == 100

    def test_below_min(self) -> None:
        assert clamp(-10) == 0

    def test_custom_range(self) -> None:
        assert clamp(50, lo=60, hi=80) == 60
        assert clamp(90, lo=60, hi=80) == 80


# 5 维度权重，跟 weights.yaml 默认值一致，加起来 1.00
_DEFAULT_WEIGHTS = {
    "limit": 0.25,
    "moneyflow": 0.25,
    "lhb": 0.20,
    "sector": 0.15,
    "sentiment": 0.15,
}


class TestWeightedCombine:
    """固定分母策略：分母 = 全部维度权重之和（缺失维度 = 0），
    奖励多维共振、惩罚单维霸榜。"""

    def test_full_coverage_4_dims(self) -> None:
        """4 维共振股（典型强势股）—— 多因子选股的真正目标。"""
        dim_scores = {"limit": 50.0, "moneyflow": 50.0, "lhb": 60.0, "sector": 30.0}
        # (50*0.25 + 50*0.25 + 60*0.20 + 30*0.15) / 1.00 = 41.5
        assert _weighted_combine(dim_scores, _DEFAULT_WEIGHTS) == 41.5

    def test_single_dim_lhb_70_gets_low_score(self) -> None:
        """仅 lhb 单维度 70 分（如可转债霸榜场景）—— 应被严重稀释。"""
        dim_scores = {"lhb": 70.0}
        # 70 * 0.20 / 1.00 = 14
        assert _weighted_combine(dim_scores, _DEFAULT_WEIGHTS) == 14.0

    def test_resonance_beats_single_dim_extreme(self) -> None:
        """核心断言：4 维共振平均 50 > 单维度 70。修复前是反过来的（70 > 49.6）。"""
        single_dim = _weighted_combine({"lhb": 70.0}, _DEFAULT_WEIGHTS)
        resonance = _weighted_combine(
            {"limit": 50.0, "moneyflow": 50.0, "lhb": 60.0, "sector": 30.0},
            _DEFAULT_WEIGHTS,
        )
        assert resonance > single_dim, (
            f"4 维共振 ({resonance}) 应高于单维霸榜 ({single_dim})"
        )

    def test_two_dim_moderate(self) -> None:
        """茅台场景：moneyflow + sector 两维度。"""
        dim_scores = {"moneyflow": 35.0, "sector": 45.0}
        # (35*0.25 + 45*0.15) / 1.00 = (8.75 + 6.75) / 1.00 = 15.5
        assert _weighted_combine(dim_scores, _DEFAULT_WEIGHTS) == 15.5

    def test_empty_scores(self) -> None:
        assert _weighted_combine({}, _DEFAULT_WEIGHTS) == 0.0

    def test_empty_weights(self) -> None:
        assert _weighted_combine({"lhb": 80.0}, {}) == 0.0

    def test_zero_total_weight_returns_zero(self) -> None:
        assert _weighted_combine({"lhb": 80.0}, {"lhb": 0.0}) == 0.0


# ============================================================================
# v2.1 plan Task 6：6 维度（新增 theme）总和 1.00 验证
# ============================================================================

# 与 weights.yaml v2.1 一致
_V2_1_DIMENSION_WEIGHTS = {
    "limit": 0.25,
    "moneyflow": 0.25,
    "lhb": 0.20,
    "sector": 0.10,
    "theme": 0.10,
    "sentiment": 0.10,
}


class TestV21SixDimensionWeights:
    def test_six_dim_weights_sum_to_one(self) -> None:
        """v2.1 后 6 维权重总和 1.0，固定分母不变。"""
        assert abs(sum(_V2_1_DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9

    def test_combine_handles_missing_theme_dim(self) -> None:
        """缺失维度按 0 计入分子但分母不缩。

        题材维度无信号时（旧数据回测 / theme ingest 失败），不影响其它维度结果。
        """
        # 4 维有分，theme 与 sentiment 缺失
        dim_scores = {"limit": 50.0, "moneyflow": 50.0, "lhb": 60.0, "sector": 30.0}
        final = _weighted_combine(dim_scores, _V2_1_DIMENSION_WEIGHTS)
        # (50*0.25 + 50*0.25 + 60*0.20 + 30*0.10) / 1.0 = 12.5+12.5+12+3 = 40.0
        assert final == 40.0

    def test_theme_dim_contributes_via_weight(self) -> None:
        """theme 维度命中时按 0.10 权重贡献。"""
        dim_scores = {"theme": 80.0}
        final = _weighted_combine(dim_scores, _V2_1_DIMENSION_WEIGHTS)
        # 80 * 0.10 / 1.0 = 8
        assert final == 8.0

    def test_resonance_with_theme_beats_no_theme(self) -> None:
        """有 theme 共振的股票 > 没 theme 共振的（验证新维度真的进入打分）。"""
        with_theme = _weighted_combine(
            {"limit": 50, "moneyflow": 50, "lhb": 60, "sector": 30, "theme": 50},
            _V2_1_DIMENSION_WEIGHTS,
        )
        without_theme = _weighted_combine(
            {"limit": 50, "moneyflow": 50, "lhb": 60, "sector": 30},
            _V2_1_DIMENSION_WEIGHTS,
        )
        assert with_theme > without_theme
        assert (with_theme - without_theme) == pytest.approx(5.0)  # 50 × 0.10
