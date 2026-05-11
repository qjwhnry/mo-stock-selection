"""调度配置与执行历史的数据库访问。

APScheduler 只负责到点触发；业务配置、启动恢复和执行审计统一落库。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

from sqlalchemy import Select, Table, func, select, update
from sqlalchemy.orm import Session

from mo_stock.storage.db import engine, get_session
from mo_stock.storage.models import (
    Base,
    SchedulerConfig,
    SchedulerRun,
    SelectionResult,
)

DEFAULT_CONFIG_ID = 1


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    """APScheduler 注册任务时使用的运行时配置。"""

    enabled: bool
    strategy: str
    skip_enhanced: bool
    skip_ai: bool
    cron_hour: int
    cron_minute: int
    timezone: str
    auto_catch_up: bool
    misfire_grace_minutes: int


def ensure_scheduler_tables() -> None:
    """创建调度相关表。

    当前项目没有 Alembic 迁移，生产库升级时只创建新增调度表，避免影响已有表。
    """
    Base.metadata.create_all(
        engine,
        tables=[
            cast(Table, SchedulerConfig.__table__),
            cast(Table, SchedulerRun.__table__),
        ],
    )


def get_or_create_config(session: Session) -> SchedulerConfig:
    """读取 id=1 配置，不存在时创建默认配置。"""
    config = session.get(SchedulerConfig, DEFAULT_CONFIG_ID)
    if config is not None:
        return config

    config = SchedulerConfig(id=DEFAULT_CONFIG_ID)
    session.add(config)
    session.flush()
    return config


def update_config(session: Session, **values: object) -> SchedulerConfig:
    """更新调度配置；值为 None 的字段会被忽略。"""
    config = get_or_create_config(session)
    for key, value in values.items():
        if value is not None:
            setattr(config, key, value)
    config.updated_at = datetime.now(UTC)
    session.flush()
    return config


def to_runtime_config(config: SchedulerConfig) -> SchedulerRuntimeConfig:
    """将 ORM 对象转换为不可变运行时配置，避免跨线程持有 Session 对象。"""
    return SchedulerRuntimeConfig(
        enabled=bool(config.enabled),
        strategy=config.strategy,
        skip_enhanced=bool(config.skip_enhanced),
        skip_ai=bool(config.skip_ai),
        cron_hour=int(config.cron_hour),
        cron_minute=int(config.cron_minute),
        timezone=config.timezone,
        auto_catch_up=bool(config.auto_catch_up),
        misfire_grace_minutes=int(config.misfire_grace_minutes),
    )


def load_runtime_config() -> SchedulerRuntimeConfig:
    """读取当前调度配置。"""
    ensure_scheduler_tables()
    with get_session() as session:
        return to_runtime_config(get_or_create_config(session))


def save_runtime_config(**values: object) -> SchedulerRuntimeConfig:
    """保存调度配置并返回运行时快照。"""
    ensure_scheduler_tables()
    with get_session() as session:
        return to_runtime_config(update_config(session, **values))


def start_run(
    *,
    trade_date: date,
    strategy: str,
    source: str,
) -> int:
    """创建 running 执行记录，返回 run_id。"""
    ensure_scheduler_tables()
    with get_session() as session:
        run = SchedulerRun(
            trade_date=trade_date,
            strategy=strategy,
            source=source,
            status="running",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        return run.id


def finish_run(run_id: int, *, status: str, error_message: str | None = None) -> None:
    """更新执行记录为 success / failed。"""
    ensure_scheduler_tables()
    with get_session() as session:
        run = session.get(SchedulerRun, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.error_message = error_message


def mark_stale_running_runs_failed(*, stale_after_minutes: int = 360) -> int:
    """将上次进程崩溃遗留的 running 记录标记为 failed。

    正常任务会在 `finish_run()` 中结束；如果进程被 kill，running 会永久残留。
    这里用较保守的 6 小时默认阈值，避免误伤仍在执行的长任务。
    """
    ensure_scheduler_tables()
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=stale_after_minutes)
    with get_session() as session:
        result = session.execute(
            update(SchedulerRun)
            .where(
                SchedulerRun.status == "running",
                SchedulerRun.started_at < cutoff,
            )
            .values(
                status="failed",
                finished_at=now,
                error_message="进程崩溃或被强制终止，未正常结束",
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


def has_successful_scheduler_run(
    session: Session,
    *,
    trade_date: date,
    strategy: str,
) -> bool:
    """判断某日某策略是否已有成功的调度执行记录。"""
    stmt = _count_stmt(SchedulerRun).where(
        SchedulerRun.trade_date == trade_date,
        SchedulerRun.strategy == strategy,
        SchedulerRun.status == "success",
    )
    return int(session.scalar(stmt) or 0) > 0


def has_selection_result(
    session: Session,
    *,
    trade_date: date,
    strategy: str,
) -> bool:
    """判断某日某策略是否已有最终选股结果，避免启动补跑重复消耗 AI。"""
    stmt = _count_stmt(SelectionResult).where(
        SelectionResult.trade_date == trade_date,
        SelectionResult.strategy == strategy,
    )
    return int(session.scalar(stmt) or 0) > 0


def _count_stmt(model: type) -> Select[tuple[int]]:
    return select(func.count()).select_from(model)
