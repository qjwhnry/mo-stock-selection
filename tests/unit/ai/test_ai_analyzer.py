"""analyze_stock_with_ai 单测（v2.2 plan Task 3）。

覆盖：
- 成功路径：mock client 返回合规 JSON → 落库 ai_analysis（字段映射正确）
- schema 校验失败：第 1 次失败重试 1 次，第 2 次仍失败 → 返回 None
- 异常路径：client 抛 BadRequestError → 返回 None（捕获不传播）
- ai_score 类型转换：float → int（schema 是 float，ORM 是 Integer）
- 字符串格式化：entry_price=1680.0 → suggested_entry="1680.00 元"
"""
from __future__ import annotations

from datetime import date

import pytest

from mo_stock.ai.analyzer import _build_dynamic_for_stock, analyze_stock_with_ai
from mo_stock.storage.models import AiAnalysis, DailyKline, StockBasic


@pytest.fixture
def basic_stock(sqlite_session):
    """fixture：插入一只测试股的 stock_basic，供 analyzer 取静态背景。"""
    sqlite_session.add(StockBasic(
        ts_code="600519.SH", symbol="600519", name="贵州茅台",
        industry="白酒", sw_l1="食品饮料", is_st=False,
        list_date=date(2001, 8, 27),
    ))
    sqlite_session.commit()
    return "600519.SH"


class TestAnalyzeStockWithAiSuccess:
    def test_writes_db_with_correct_field_mapping(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """成功路径：score float → int、entry_price float → "X.XX 元"。"""
        mock_claude_client.analyze.return_value = (
            '{"ts_code":"600519.SH","score":85.7,'
            '"thesis":"北向资金连续净买入，叠加白酒板块短线反弹，短线量价结构改善。",'
            '"entry_price":1680.0,"stop_loss":1640.0,'
            '"key_signals":["北向连续净买入","白酒板块涨幅 TOP 3"],'
            '"risks":["大盘回调风险"]}',
            {"input_tokens": 1000, "output_tokens": 300,
             "cache_creation_tokens": 800, "cache_read_tokens": 0},
        )
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is not None
        assert result.score == 85.7  # pydantic 模型保留 float

        # 落库 ai_analysis
        from sqlalchemy import select
        row = sqlite_session.execute(
            select(AiAnalysis)
            .where(AiAnalysis.ts_code == "600519.SH")
            .where(AiAnalysis.trade_date == date(2026, 4, 24))
        ).scalar_one()

        # 字段映射断言（按 v2.2 plan §2.1 映射表）
        assert row.ai_score == 86  # round(85.7) → Integer
        assert "北向" in row.thesis
        assert row.key_catalysts == ["北向连续净买入", "白酒板块涨幅 TOP 3"]
        assert row.risks == ["大盘回调风险"]
        assert row.suggested_entry == "1680.00 元"
        assert row.stop_loss == "1640.00 元"
        assert row.input_tokens == 1000
        assert row.output_tokens == 300
        assert row.cache_creation_tokens == 800
        assert row.cache_read_tokens == 0

    def test_handles_null_entry_and_stop(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """entry_price/stop_loss 为 null 时落库为 None（不是空字符串）。"""
        mock_claude_client.analyze.return_value = (
            '{"ts_code":"600519.SH","score":50,'
            '"thesis":"占位测试论点，验证 null 字段映射不报错。",'
            '"entry_price":null,"stop_loss":null,'
            '"key_signals":[],"risks":[]}',
            {"input_tokens": 100, "output_tokens": 50,
             "cache_creation_tokens": 0, "cache_read_tokens": 0},
        )
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is not None

        from sqlalchemy import select
        row = sqlite_session.execute(
            select(AiAnalysis)
        ).scalar_one()
        assert row.suggested_entry is None
        assert row.stop_loss is None


class TestAnalyzeStockWithAiFailure:
    def test_invalid_json_retries_once_then_returns_none(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """JSON 解析失败 → 重试 1 次 → 仍失败 → 返回 None（不写库）。"""
        mock_claude_client.analyze.side_effect = [
            ("not valid json at all", {"input_tokens": 100, "output_tokens": 10,
                                       "cache_creation_tokens": 0, "cache_read_tokens": 0}),
            ("still bad", {"input_tokens": 100, "output_tokens": 10,
                           "cache_creation_tokens": 0, "cache_read_tokens": 0}),
        ]
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is None
        # 调了 2 次（重试 1 次）
        assert mock_claude_client.analyze.call_count == 2

        # 不应有 ai_analysis 行写入
        from sqlalchemy import select
        rows = sqlite_session.execute(select(AiAnalysis)).scalars().all()
        assert len(rows) == 0

    def test_schema_violation_retries_once_then_returns_none(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """JSON 合法但 schema 越界（score=120）→ 重试 1 次 → 失败返回 None。"""
        bad_json = '{"ts_code":"600519.SH","score":120,"thesis":"score 越界 0-100，应触发 ValidationError 重试。"}'
        mock_claude_client.analyze.side_effect = [
            (bad_json, {"input_tokens": 100, "output_tokens": 10,
                        "cache_creation_tokens": 0, "cache_read_tokens": 0}),
            (bad_json, {"input_tokens": 100, "output_tokens": 10,
                        "cache_creation_tokens": 0, "cache_read_tokens": 0}),
        ]
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is None
        assert mock_claude_client.analyze.call_count == 2

    def test_client_exception_returns_none(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """SDK 异常（如 BadRequestError 或 5xx 重试耗尽）→ 返回 None，不传播。"""
        mock_claude_client.analyze.side_effect = RuntimeError("simulated SDK failure")
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is None

    def test_first_attempt_succeeds_no_retry(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """第一次就合规时不重试。"""
        mock_claude_client.analyze.return_value = (
            '{"ts_code":"600519.SH","score":75,'
            '"thesis":"测试一次成功，不应触发重试逻辑，验证 mock 路径正常工作。",'
            '"entry_price":null,"stop_loss":null,'
            '"key_signals":[],"risks":[]}',
            {"input_tokens": 100, "output_tokens": 50,
             "cache_creation_tokens": 0, "cache_read_tokens": 0},
        )
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is not None
        assert mock_claude_client.analyze.call_count == 1

    def test_client_init_failure_returns_none(
        self, sqlite_session, monkeypatch, basic_stock,
    ) -> None:
        """未配置 API Key 等 client 初始化失败 → 降级返回 None，不中断主流程。"""
        monkeypatch.setattr(
            "mo_stock.ai.analyzer._get_claude_client",
            lambda: (_ for _ in ()).throw(ValueError("ANTHROPIC_API_KEY 未配置")),
        )

        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )

        assert result is None


class TestBuildDynamicForStock:
    def test_amount_qy_converts_to_yi(self, sqlite_session, basic_stock) -> None:
        """DailyKline.amount 单位是千元，100000 千元应渲染为 1.0 亿元。"""
        td = date(2026, 4, 24)
        sqlite_session.add(DailyKline(
            ts_code=basic_stock,
            trade_date=td,
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            pre_close=10.0,
            pct_chg=2.0,
            vol=100_000.0,
            amount=100_000.0,
        ))
        sqlite_session.commit()

        prompt = _build_dynamic_for_stock(sqlite_session, basic_stock, td, {})

        assert "<amount_yi>1.0</amount_yi>" in prompt


class TestSwingAiCallPath:
    """Swing AI 调用路径：strategy=swing 走 swing prompt + 落库 strategy=swing。"""

    def test_swing_strategy_uses_swing_prompt(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        mock_claude_client.analyze.return_value = (
            '{"ts_code":"600519.SH","score":78.0,'
            '"thesis":"趋势延续，波段资金持续流入，行业共振。MA 多头排列确认趋势强度，资金 5 日持续净流入验证主力意图，行业板块同步走强形成共振效应。",'
            '"entry_price":1680.0,"stop_loss":1640.0,'
            '"key_signals":["MA 多头排列","资金 5 日持续净流入"],'
            '"risks":["大盘回调风险"]}',
            {"input_tokens": 1000, "output_tokens": 300,
             "cache_creation_tokens": 800, "cache_read_tokens": 0},
        )
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
            strategy="swing",
            regime_score=65.0,
        )
        assert result is not None
        assert result.score == 78.0

        from sqlalchemy import select
        row = sqlite_session.execute(
            select(AiAnalysis)
            .where(AiAnalysis.ts_code == "600519.SH")
        ).scalar_one()
        assert row.strategy == "swing"
        assert row.ai_score == 78

    def test_swing_dynamic_contains_regime_and_ma20(
        self, sqlite_session, basic_stock,
    ) -> None:
        td = date(2026, 4, 24)
        # 插入 21 天 K 线以计算 MA20 和 ATR
        from datetime import timedelta
        for i in range(21):
            d = td - timedelta(days=21 - i)
            sqlite_session.add(DailyKline(
                ts_code=basic_stock, trade_date=d,
                open=10.0 + i * 0.1, high=10.5 + i * 0.1,
                low=9.8 + i * 0.1, close=10.2 + i * 0.1,
                pre_close=10.0, pct_chg=1.0, vol=1e5, amount=1e5,
            ))
        sqlite_session.commit()

        prompt = _build_dynamic_for_stock(
            sqlite_session, basic_stock, td, {},
            strategy="swing", regime_score=55.0,
        )
        assert "<regime_score>55.0</regime_score>" in prompt
        assert "<ma20>" in prompt
        assert "缺失" not in prompt.split("<ma20>")[1].split("</ma20>")[0]

    def test_ts_code_mismatch_rejected(
        self, sqlite_session, mock_claude_client, basic_stock,
    ) -> None:
        """AI 返回的 ts_code 与请求不匹配时拒绝，重试后仍错则返回 None。"""
        mock_claude_client.analyze.side_effect = [
            ('{"ts_code":"000001.SZ","score":80,'
             '"thesis":"这是一个长度足够的测试论点，用于验证代码不匹配时会被拒绝。",'
             '"entry_price":null,"stop_loss":null,"key_signals":[],"risks":[]}',
             {"input_tokens": 100, "output_tokens": 50,
              "cache_creation_tokens": 0, "cache_read_tokens": 0}),
            ('{"ts_code":"000002.SZ","score":70,'
             '"thesis":"第二次返回仍然不匹配，验证重试后仍返回 None 的逻辑。",'
             '"entry_price":null,"stop_loss":null,"key_signals":[],"risks":[]}',
             {"input_tokens": 100, "output_tokens": 50,
              "cache_creation_tokens": 0, "cache_read_tokens": 0}),
        ]
        result = analyze_stock_with_ai(
            sqlite_session, basic_stock, date(2026, 4, 24),
            rule_dim_scores={},
        )
        assert result is None
        assert mock_claude_client.analyze.call_count == 2
