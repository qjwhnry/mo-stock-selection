"""scorer.combine._final_score_from 单测（P0-2）。

MVP 阶段 ai_score 永远为 None，但 final_score 列必须仍是数值（不能 None）。
Phase 3 接通 AI 后，传入数值 ai_score 即按权重融合。
"""
from __future__ import annotations

import pytest

from mo_stock.scorer.combine import _final_score_from, _name_is_st


class TestFinalScoreFrom:
    def test_ai_none_returns_rule_score(self) -> None:
        """MVP 阶段：ai_score=None → final = rule（不重缩放）。"""
        assert _final_score_from(rule_score=42.5, ai_score=None) == 42.5

    def test_ai_value_blends_by_default_weights(self) -> None:
        """Phase 3 默认权重 rule=0.6 / ai=0.4。"""
        # rule=50, ai=80 → (50*0.6 + 80*0.4) / 1.0 = 30 + 32 = 62
        assert _final_score_from(rule_score=50.0, ai_score=80.0) == pytest.approx(62.0)

    def test_zero_total_weight_falls_back_to_rule(self) -> None:
        """如果两个权重都是 0（配置异常），降级为 rule 分。"""
        assert _final_score_from(50.0, 80.0, rule_weight=0.0, ai_weight=0.0) == 50.0

    def test_custom_weights(self) -> None:
        """自定义权重场景（如未来灰度切到 rule=0.4 / ai=0.6）。"""
        # rule=60, ai=40 → (60*0.4 + 40*0.6) / 1.0 = 24 + 24 = 48
        assert _final_score_from(60.0, 40.0, rule_weight=0.4, ai_weight=0.6) == pytest.approx(48.0)


class TestNameIsSt:
    """P2-6：ST 名称识别用 regex 替代 startswith。"""

    @pytest.mark.parametrize("name", [
        "*ST天龙",
        "ST康美",
        "*st 天龙",     # 大小写
        "  *ST 康美  ", # 首尾空格
    ])
    def test_st_names_recognized(self, name: str) -> None:
        assert _name_is_st(name) is True

    @pytest.mark.parametrize("name", [
        "贵州茅台",
        "中国平安",
        "STAR-V WW",    # 含 "STA" 不是 ST 前缀，正则正确放过
    ])
    def test_normal_names_filtered(self, name: str) -> None:
        assert _name_is_st(name) is False

    def test_empty_or_none(self) -> None:
        assert _name_is_st(None) is False
        assert _name_is_st("") is False
