"""FastAPI 依赖注入。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from mo_stock.storage.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI Depends 用的 Session 生成器。

    与 CLI 的 get_session() 不同，这里不自动 commit（纯读 API），
    退出时只 close。
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
