"""Alembic 迁移入口。

- 从 config.settings 读数据库连接串（不写死在 alembic.ini）
- target_metadata 指向项目 ORM 的 Base.metadata，以启用 --autogenerate
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# 项目的 ORM metadata 与运行时配置
from config.settings import settings
from mo_stock.storage.models import Base

# Alembic Config 对象
config = context.config

# 读取日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 依赖的 metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只打印 SQL，不连 DB。用于 CI 或 review diff。"""
    context.configure(
        url=settings.db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连 DB 执行 migrations。"""
    connectable = create_engine(settings.db_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
