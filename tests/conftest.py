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
