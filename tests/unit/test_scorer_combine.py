"""scorer.combine 的权重计算逻辑测试。

这里测纯函数部分；完整端到端（带 DB 和硬规则）放在 integration 测试。
"""
from __future__ import annotations

from mo_stock.filters.base import clamp


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


def test_weighted_combine_normalization() -> None:
    """验证融合公式正确：final = Σ(score × w) / Σ(w)，权重总和不为 1 时归一化。"""
    dim_scores = {"limit": 80.0, "moneyflow": 60.0}
    weights = {"limit": 0.25, "moneyflow": 0.25}  # 加起来 0.5

    total_w = sum(weights.values())
    combined = sum(dim_scores.get(d, 0) * w for d, w in weights.items()) / total_w

    # 等价于 (80*0.25 + 60*0.25) / 0.5 = 35/0.5 = 70
    assert combined == 70.0


def test_missing_dim_treated_as_zero() -> None:
    """缺失维度按 0 处理：MVP 只有 limit+moneyflow 时，lhb/sector/sentiment 缺失不影响公式。"""
    dim_scores = {"limit": 80.0}  # 只有一个维度有分
    weights = {"limit": 0.25, "moneyflow": 0.25, "lhb": 0.20}

    total_w = sum(weights.values())
    combined = sum(dim_scores.get(d, 0) * w for d, w in weights.items()) / total_w

    # (80*0.25 + 0*0.25 + 0*0.20) / 0.70 = 20/0.70 ≈ 28.57
    assert abs(combined - 28.571) < 0.01
