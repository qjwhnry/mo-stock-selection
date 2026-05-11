"""调度状态持久化层测试。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mo_stock.scheduler import state
from mo_stock.storage.models import SchedulerRun


@pytest.fixture()
def scheduler_state_db(monkeypatch, tmp_path) -> Iterator[None]:
    """用临时 SQLite 测试 scheduler_config / scheduler_run。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.sqlite'}", future=True)

    @contextmanager
    def get_session() -> Iterator[Session]:
        session = Session(bind=engine, future=True)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(state, "engine", engine)
    monkeypatch.setattr(state, "get_session", get_session)
    state.ensure_scheduler_tables()
    yield
    engine.dispose()


def test_start_and_finish_run(scheduler_state_db) -> None:
    run_id = state.start_run(
        trade_date=date(2026, 5, 11),
        strategy="short",
        source="scheduled",
    )
    state.finish_run(run_id, status="success")

    with state.get_session() as session:
        run = session.get(SchedulerRun, run_id)
        assert run is not None
        assert run.status == "success"
        assert run.finished_at is not None


def test_has_successful_scheduler_run(scheduler_state_db) -> None:
    trade_date = date(2026, 5, 11)
    run_id = state.start_run(
        trade_date=trade_date,
        strategy="swing",
        source="catch_up",
    )
    state.finish_run(run_id, status="success")

    with state.get_session() as session:
        assert state.has_successful_scheduler_run(
            session, trade_date=trade_date, strategy="swing",
        )
        assert not state.has_successful_scheduler_run(
            session, trade_date=trade_date, strategy="short",
        )


def test_mark_stale_running_runs_failed(scheduler_state_db) -> None:
    now = datetime.now(UTC)
    stale = SchedulerRun(
        trade_date=date(2026, 5, 11),
        strategy="short",
        source="scheduled",
        status="running",
        started_at=now - timedelta(hours=8),
    )
    fresh = SchedulerRun(
        trade_date=date(2026, 5, 12),
        strategy="short",
        source="scheduled",
        status="running",
        started_at=now,
    )
    with state.get_session() as session:
        session.add_all([stale, fresh])

    updated = state.mark_stale_running_runs_failed(stale_after_minutes=360)

    with state.get_session() as session:
        rows = session.scalars(select(SchedulerRun).order_by(SchedulerRun.trade_date)).all()
        assert updated == 1
        assert rows[0].status == "failed"
        assert rows[0].error_message == "进程崩溃或被强制终止，未正常结束"
        assert rows[1].status == "running"
