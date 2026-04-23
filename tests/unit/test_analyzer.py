"""analyzer 纯函数测试。

DB 相关的端到端路径由 integration 测试覆盖；这里仅验证
`_combine_rule_score` 的加权/缺失处理逻辑，与 `scorer.combine` 保持一致。
"""
from __future__ import annotations

from mo_stock.analyzer import _combine_rule_score


def test_combine_rule_score_two_dims() -> None:
    """两个维度都有分：分子/分母都只算这两个，与 combine_scores 公式一致。"""
    dim_scores = {
        "limit": {"score": 80.0, "detail": {}},
        "moneyflow": {"score": 60.0, "detail": {}},
    }
    weights = {"limit": 0.25, "moneyflow": 0.25, "lhb": 0.20}

    # 分母 = 0.25 + 0.25 = 0.5，分子 = 80*0.25 + 60*0.25 = 35
    assert _combine_rule_score(dim_scores, weights) == 70.0


def test_combine_rule_score_zero_dim_excluded() -> None:
    """score == 0 的维度视为缺失，不参与加权。"""
    dim_scores = {
        "limit": {"score": 0.0, "detail": {}},      # 0 分视为缺失
        "moneyflow": {"score": 60.0, "detail": {}},
    }
    weights = {"limit": 0.25, "moneyflow": 0.25}

    # 只有 moneyflow 参与：60 * 0.25 / 0.25 = 60
    assert _combine_rule_score(dim_scores, weights) == 60.0


def test_combine_rule_score_empty() -> None:
    """该股完全无得分时返回 0，不应抛异常。"""
    assert _combine_rule_score({}, {"limit": 0.25}) == 0.0


def test_combine_rule_score_weight_missing_for_scored_dim() -> None:
    """维度有分但权重表里没有：该维度不计入分母，避免 KeyError。"""
    dim_scores = {"unknown_dim": {"score": 50.0, "detail": {}}}
    weights = {"limit": 0.25, "moneyflow": 0.25}

    assert _combine_rule_score(dim_scores, weights) == 0.0
