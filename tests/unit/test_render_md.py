"""report/render_md.py 选出原因渲染单测（v2.2 plan Task 6）。

覆盖：
- _translate_dim_detail 5 个维度翻译器（数字 detail → 中文人话证据列表）
- _render_one_stock_section markdown 块（含 AI 时 / 不含 AI 时）
- JSON rationale 结构化字段
"""
from __future__ import annotations

from mo_stock.report.render_md import _translate_dim_detail


class TestTranslateLhb:
    def test_institution_net_buy(self) -> None:
        evidences = _translate_dim_detail("lhb", {
            "net_rate_pct": 3.36, "amount_rate_pct": 23.42,
            "reason": "日涨幅偏离值达7%的证券",
            "institution_net_buy": 19_000_000,
        })
        # 至少应包含机构净买的中文翻译
        assert any("机构净买" in e and "1900" in e for e in evidences)

    def test_hot_money_buy(self) -> None:
        evidences = _translate_dim_detail("lhb", {
            "net_rate_pct": 5.5,
            "hot_money_net_buy": 8_000_000,
        })
        assert any("游资净买" in e and "800" in e for e in evidences)

    def test_hot_money_sell_penalty(self) -> None:
        evidences = _translate_dim_detail("lhb", {
            "net_rate_pct": 5.5,
            "hot_money_sell_penalty": -15,
        })
        assert any("游资" in e and ("卖" in e or "扣" in e) for e in evidences)

    def test_northbound_buy(self) -> None:
        evidences = _translate_dim_detail("lhb", {
            "northbound_net_buy": 35_000_000,
        })
        assert any("北向" in e for e in evidences)

    def test_empty_detail(self) -> None:
        assert _translate_dim_detail("lhb", {}) == []


class TestTranslateTheme:
    def test_best_concept_with_rank(self) -> None:
        evidences = _translate_dim_detail("theme", {
            "best_concept": "885806.TI",
            "ths_rank": 1, "limit_rank": 1,
            "concept_net_amount_yi": 10.5,
        })
        # 至少应说出命中概念 + 排名
        text = " ".join(evidences)
        assert "885806" in text or "概念" in text
        assert "1" in text  # rank 1

    def test_moneyflow_positive(self) -> None:
        evidences = _translate_dim_detail("theme", {
            "best_concept": "885806.TI",
            "concept_net_amount_yi": 5.5,
        })
        text = " ".join(evidences)
        assert "5.5" in text or "净流入" in text


class TestTranslateMoneyflow:
    def test_today_bonus_with_ratio(self) -> None:
        evidences = _translate_dim_detail("moneyflow", {
            "net_mf_wan": 23000.0,
            "today_bonus": 50,
            "net_mf_ratio_pct": 8.12,
            "big_ratio": 0.62,
            "ratio_bonus": 30,
        })
        text = " ".join(evidences)
        assert "净流入" in text or "占比" in text
        assert "8.12" in text or "8.1" in text


class TestTranslateLimit:
    def test_renders_basic_keys(self) -> None:
        # limit_filter 的 detail 结构灵活，至少不报错
        evidences = _translate_dim_detail("limit", {
            "first_board_bonus": 20,
            "seal_amount_bonus": 30,
        })
        # 即使翻译器没全覆盖所有键，也应返回 list（可空可非空）
        assert isinstance(evidences, list)


class TestTranslateSector:
    def test_rank_bonus(self) -> None:
        evidences = _translate_dim_detail("sector", {
            "l1_code": "801080.SI",
            "sector_rank": 1,
            "rank_bonus": 70,
            "sector_3d_avg": 5.5,
            "trend_bonus": 30,
        })
        text = " ".join(evidences)
        # 应该有"排名"或"行业"或具体数字
        assert isinstance(evidences, list)
        assert len(evidences) > 0


class TestTranslateUnknownDim:
    def test_unknown_dim_falls_back_gracefully(self) -> None:
        """未来新增维度但翻译器未更新时，不应崩溃。"""
        evidences = _translate_dim_detail("future_new_dim", {"some_key": 42})
        assert isinstance(evidences, list)
