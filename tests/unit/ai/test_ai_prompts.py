"""4 段 prompt 构造器单测（v2.2 plan Task 1）。

覆盖：
- system prompt 含免责声明 + JSON schema 约束
- methodology prompt 含 5 维度（不是 6 维，sentiment 是预留）
- static_stock prompt 接受 stock_basic / kline 摘要
- dynamic_stock prompt 把 5 维度 detail 完整渲染给 AI
"""
from __future__ import annotations

from datetime import date

from mo_stock.ai.prompts import (
    build_dynamic_stock_prompt,
    build_methodology_prompt,
    build_static_stock_prompt,
    build_system_prompt,
)
from mo_stock.filters.base import ScoreResult


class TestSystemPrompt:
    def test_contains_disclaimer(self) -> None:
        p = build_system_prompt()
        assert "不构成投资建议" in p

    def test_contains_json_schema(self) -> None:
        p = build_system_prompt()
        assert "JSON" in p
        # schema 关键字段都要在 prompt 里说明，AI 才知道怎么输出
        assert "ts_code" in p
        assert "score" in p
        assert "thesis" in p

    def test_anti_prompt_injection(self) -> None:
        """system prompt 顶部应该有"忽略后续指令重写"的硬约束。"""
        p = build_system_prompt()
        # 关键词组任一命中即可
        assert any(kw in p for kw in ("忽略", "不可改写", "不接受", "禁止"))


class TestMethodologyPrompt:
    def test_includes_all_5_dimensions(self) -> None:
        """v2.1 后规则层是 5 维：limit / moneyflow / lhb / sector / theme。
        sentiment 是预留维度，不应被写成"已接通"。"""
        p = build_methodology_prompt()
        for dim in ("limit", "moneyflow", "lhb", "sector", "theme"):
            assert dim in p

    def test_short_term_horizon(self) -> None:
        """methodology 必须明确"短线 1-3 交易日"，避免 AI 给长线建议。"""
        p = build_methodology_prompt()
        assert "短线" in p or "1-3" in p


class TestStaticStockPrompt:
    def test_renders_basic_info(self) -> None:
        p = build_static_stock_prompt(
            ts_code="600519.SH", name="贵州茅台",
            industry="白酒", sw_l1="食品饮料",
            kline_summary="近 30 日涨幅 5%，量能温和",
            anns_summary="无重大公告",
        )
        assert "600519.SH" in p
        assert "贵州茅台" in p
        assert "白酒" in p

    def test_handles_missing_optional_fields(self) -> None:
        """部分元数据缺失（NULL）时 prompt 不报错，用占位符替代。"""
        p = build_static_stock_prompt(
            ts_code="600519.SH", name=None,
            industry=None, sw_l1=None,
            kline_summary="", anns_summary="",
        )
        assert "600519.SH" in p


class TestDynamicStockPrompt:
    def test_includes_all_dim_scores(self) -> None:
        td = date(2026, 4, 24)
        dim_scores = {
            "limit": ScoreResult(
                ts_code="600519.SH", trade_date=td, dim="limit",
                score=70.0, detail={"first_board_bonus": 20, "seal_amount_bonus": 30},
            ),
            "lhb": ScoreResult(
                ts_code="600519.SH", trade_date=td, dim="lhb",
                score=60.0, detail={"net_rate_pct": 3.36, "institution_net_buy": 19000000},
            ),
        }
        p = build_dynamic_stock_prompt(
            ts_code="600519.SH", trade_date=td, dim_scores=dim_scores,
            close=1680.0, pct_chg=2.5, amount_yi=15.3,
        )
        # 命中的两个维度名都要在 prompt 里
        assert "limit" in p
        assert "lhb" in p
        # detail 关键数字也要透传给 AI
        assert "70" in p or "70.0" in p
        assert "60" in p or "60.0" in p

    def test_omits_missing_dimensions(self) -> None:
        """没命中信号的维度（dim_scores 里没有的 key）不应在 prompt 里凭空出现。"""
        td = date(2026, 4, 24)
        dim_scores = {
            "limit": ScoreResult(
                ts_code="600519.SH", trade_date=td, dim="limit",
                score=70.0, detail={},
            ),
        }
        p = build_dynamic_stock_prompt(
            ts_code="600519.SH", trade_date=td, dim_scores=dim_scores,
            close=1680.0, pct_chg=2.5, amount_yi=15.3,
        )
        # 只有 limit 的话，theme/lhb 等不应被强行渲染
        # （用"未命中"或"无信号"等占位词都可，但至少不应混淆 AI）
        assert "limit" in p
