"""共享 pytest fixtures。"""
from __future__ import annotations

from datetime import date

import pytest


@pytest.fixture
def sample_trade_date() -> date:
    """一个用于测试的固定交易日。"""
    return date(2026, 4, 22)


def _register_jsonb_on_sqlite() -> None:
    """给 SQLite dialect 打补丁，使 JSONB 编译为 JSON（PG 专用类型在 SQLite 测试时降级）。

    SQLite 不支持 JSONB；create_all 会报 CompileError。本函数注册 visit_JSONB
    把 JSONB 透明转 JSON。供任何需要在 SQLite 上跑 ORM 的测试 fixture 调用。
    """
    from sqlalchemy.dialects.sqlite import base as sqlite_base
    from sqlalchemy.types import JSON

    if not hasattr(sqlite_base.SQLiteTypeCompiler, "visit_JSONB"):
        def visit_JSONB(self, type_, **kw):  # noqa: N802
            return self.process(JSON(), **kw)

        sqlite_base.SQLiteTypeCompiler.visit_JSONB = visit_JSONB


@pytest.fixture
def sqlite_session(tmp_path):
    """临时 SQLite 文件库 + 全部 ORM 表 + Session。

    用于 ThemeFilter / LhbFilter 等需要查询 ORM 的单测。
    不测试 upsert（PG 专用 ON CONFLICT），调用方用 session.add 直接写入。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from mo_stock.storage.models import Base

    _register_jsonb_on_sqlite()
    db_file = tmp_path / "test.sqlite"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def mock_claude_client(monkeypatch):
    """统一 mock ClaudeClient.analyze() 用于 AI 模块单测（v2.2 plan §2.5）。

    所有 AI 测试通过本 fixture 注入；改 client 路径时只改这一处，
    禁止每个测试文件各自 monkeypatch 不同路径。

    用法：
        def test_xxx(mock_claude_client):
            mock_claude_client.analyze.return_value = ('{"score":85,...}', {"input_tokens": 1000, ...})
            result = analyze_stock_with_ai(...)
            mock_claude_client.analyze.assert_called_once()
    """
    from unittest.mock import MagicMock
    fake = MagicMock()
    # 默认 return_value 给 schema 合规的最小输出，单测可在自己作用域内覆盖
    fake.analyze.return_value = (
        '{"ts_code":"600519.SH","score":50,"thesis":"占位输出，单测请自行覆盖 mock，否则将得到此默认值。",'
        '"entry_price":null,"stop_loss":null,"key_signals":[],"risks":[]}',
        {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0},
    )
    # patch analyzer.py 的 client 工厂入口（按 v2.2 plan §2.4 / §0.2.1 第 12 条）
    monkeypatch.setattr("mo_stock.ai.analyzer._get_claude_client", lambda: fake)
    return fake
