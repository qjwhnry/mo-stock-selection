"""StockAiAnalysis pydantic schema 单测（v2.2 plan Task 1）。

输出契约：ts_code / score / thesis / entry_price / stop_loss / key_signals / risks。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mo_stock.ai.schemas import StockAiAnalysis


class TestStockAiAnalysisValid:
    def test_minimal_required_fields(self) -> None:
        obj = StockAiAnalysis(
            ts_code="600519.SH", score=85.0,
            thesis="北向资金连续 3 日净买入，叠加白酒板块短线反弹，技术面突破 60 日均线。",
        )
        assert obj.score == 85.0
        assert obj.entry_price is None
        assert obj.key_signals == []

    def test_full_fields(self) -> None:
        obj = StockAiAnalysis(
            ts_code="000001.SZ", score=72.5,
            thesis="平安银行短线突破，叠加金融板块强势，可关注。",
            entry_price=11.5, stop_loss=11.2,
            key_signals=["北向连续净买入", "金融板块涨幅 TOP 3"],
            risks=["大盘回调风险"],
        )
        assert obj.entry_price == 11.5
        assert len(obj.key_signals) == 2

    def test_bj_ts_code(self) -> None:
        """北交所 .BJ 也应被接受。"""
        obj = StockAiAnalysis(
            ts_code="430139.BJ", score=50,
            thesis="测试用例：北交所代码应该通过 ts_code 正则校验。",
        )
        assert obj.ts_code == "430139.BJ"


class TestStockAiAnalysisRejects:
    def test_invalid_ts_code(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="INVALID", score=85, thesis="x" * 30)

    def test_ts_code_no_suffix(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="600519", score=85, thesis="x" * 30)

    def test_score_above_100(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="600519.SH", score=120, thesis="x" * 30)

    def test_score_negative(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="600519.SH", score=-5, thesis="x" * 30)

    def test_thesis_too_short(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="600519.SH", score=50, thesis="太短")

    def test_thesis_too_long(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(ts_code="600519.SH", score=50, thesis="x" * 600)

    def test_negative_entry_price(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(
                ts_code="600519.SH", score=50, thesis="x" * 30,
                entry_price=-1.0,
            )

    def test_too_many_key_signals(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(
                ts_code="600519.SH", score=50, thesis="x" * 30,
                key_signals=["a", "b", "c", "d", "e", "f"],  # max 5
            )

    def test_too_many_risks(self) -> None:
        with pytest.raises(ValidationError):
            StockAiAnalysis(
                ts_code="600519.SH", score=50, thesis="x" * 30,
                risks=["a", "b", "c", "d"],  # max 3
            )
