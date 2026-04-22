"""SQLAlchemy Engine / Session 工厂。

统一入口：
    from mo_stock.storage.db import engine, SessionLocal, get_session

单例 Engine 使用 psycopg2 同步驱动，契合 APScheduler 的同步任务模型。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings


def _make_engine() -> Engine:
    """根据 settings.db_url 创建 Engine。

    - `pool_pre_ping=True`：连接池自动探活，避免长时间空闲被 PG 关闭导致的 stale 连接。
    - `pool_recycle=3600`：每 1 小时回收一次连接。
    - `future=True`：SQLAlchemy 2.x 风格。
    """
    return create_engine(
        settings.db_url,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
        echo=False,  # 需要查看 SQL 时临时改 True
    )


# 进程级单例 Engine
engine: Engine = _make_engine()

# Session 工厂：autocommit=False、autoflush=False 是 SQLAlchemy 2.x 推荐做法
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # commit 后对象仍可用
    future=True,
)


@contextmanager
def get_session() -> Iterator[Session]:
    """上下文管理器：自动 commit/rollback/close。

    用法：
        with get_session() as session:
            session.add(obj)
            # 无需手动 commit，退出时自动提交；异常时自动回滚
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
