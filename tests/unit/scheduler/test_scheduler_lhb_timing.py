"""调度层时点保护单测（P0-1）。

龙虎榜数据由交易所于 T 日 ~15:30 后发布。若调度被运维误提前到盘中
（如 14:00），LhbFilter 会用"还没出"的龙虎榜信号 → look-ahead bias。

`_assert_lhb_data_available` 仅在 trade_date == 当日 时校验，
回填历史日期不触发。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from mo_stock.scheduler.daily_job import CN_TZ, _assert_lhb_data_available, _build_filters


class TestAssertLhbDataAvailable:
    def test_after_1530_passes(self) -> None:
        """当日 15:30 后调用，正常通过。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 15, 31, tzinfo=CN_TZ)
        _assert_lhb_data_available(today, now=now)  # 不抛

    def test_before_1530_blocks(self) -> None:
        """当日 15:30 前调用，立即 RuntimeError 阻止运行。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 14, 0, tzinfo=CN_TZ)
        with pytest.raises(RuntimeError, match="早于龙虎榜发布时间"):
            _assert_lhb_data_available(today, now=now)

    def test_at_exactly_1530_passes(self) -> None:
        """边界：15:30:00 整点视为已发布（与时点保护边界一致）。"""
        today = date.today()
        now = datetime(today.year, today.month, today.day, 15, 30, tzinfo=CN_TZ)
        _assert_lhb_data_available(today, now=now)  # 不抛

    def test_historical_backfill_not_checked(self) -> None:
        """回填历史日期：盘中调用也允许（数据早就出了）。"""
        yesterday = date.today() - timedelta(days=1)
        # 即便用"今天 10:00"作为 now，trade_date 是昨天 → 不校验时点
        now = datetime.now(CN_TZ).replace(hour=10, minute=0, second=0, microsecond=0)
        _assert_lhb_data_available(yesterday, now=now)  # 不抛


def test_scheduler_builds_swing_filters_and_dims() -> None:
    filters, dims = _build_filters("swing", {})

    assert dims == [
        "trend", "pullback", "moneyflow_swing", "sector_swing",
        "theme_swing", "catalyst", "risk_liquidity",
    ]
    assert [f.dim for f in filters] == dims


def test_run_daily_pipeline_with_history_records_success(monkeypatch) -> None:
    """调度执行成功时写入 success。"""
    from mo_stock.scheduler import daily_job

    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(daily_job, "start_run", lambda **kwargs: 7)
    monkeypatch.setattr(
        daily_job,
        "run_daily_pipeline",
        lambda **kwargs: calls.append(("pipeline", kwargs)),
    )
    monkeypatch.setattr(
        daily_job,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", (run_id, kwargs))),
    )

    trade_date = date(2026, 5, 11)
    daily_job.run_daily_pipeline_with_history(
        trade_date=trade_date,
        skip_ai=True,
        strategy="short",
        source="scheduled",
    )

    assert calls[0] == ("pipeline", {
        "trade_date": trade_date,
        "skip_enhanced": False,
        "skip_ai": True,
        "strategy": "short",
    })
    assert calls[1] == ("finish", (7, {"status": "success"}))


def test_run_daily_pipeline_with_history_records_failure(monkeypatch) -> None:
    """调度执行失败时写入 failed 并继续抛出异常，交给 APScheduler 记录。"""
    from mo_stock.scheduler import daily_job

    calls: list[tuple[int, dict]] = []

    def _boom(**kwargs) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(daily_job, "start_run", lambda **kwargs: 8)
    monkeypatch.setattr(daily_job, "run_daily_pipeline", _boom)
    monkeypatch.setattr(
        daily_job,
        "finish_run",
        lambda run_id, **kwargs: calls.append((run_id, kwargs)),
    )

    with pytest.raises(RuntimeError, match="boom"):
        daily_job.run_daily_pipeline_with_history(trade_date=date(2026, 5, 11))

    assert calls == [(8, {"status": "failed", "error_message": "boom"})]


def test_register_scheduler_jobs_uses_configured_timezone(monkeypatch) -> None:
    """CronTrigger 必须按配置时区排程，避免 Docker UTC 环境把 21:00 解释成 UTC。"""
    from mo_stock.scheduler import daily_job
    from mo_stock.scheduler.state import SchedulerRuntimeConfig

    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.jobs.append({"args": args, "kwargs": kwargs})

    runtime_cfg = SchedulerRuntimeConfig(
        enabled=True,
        strategy="short",
        skip_enhanced=False,
        skip_ai=True,
        cron_hour=21,
        cron_minute=10,
        timezone="Asia/Shanghai",
        auto_catch_up=False,
        misfire_grace_minutes=60,
    )
    scheduler = FakeScheduler()

    daily_job.register_scheduler_jobs(scheduler, runtime_cfg)

    trigger = scheduler.jobs[0]["kwargs"]["trigger"]
    assert str(trigger.timezone) == "Asia/Shanghai"


def test_register_catch_up_job_when_missed_within_grace(monkeypatch) -> None:
    """启动时间错过调度点但仍在宽限期内时，注册一次性补跑。"""
    from mo_stock.scheduler import daily_job
    from mo_stock.scheduler.state import SchedulerRuntimeConfig

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 5, 11, 21, 5, tzinfo=tz)

    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.jobs.append({"args": args, "kwargs": kwargs})

    @contextmanager
    def fake_session():
        yield object()

    runtime_cfg = SchedulerRuntimeConfig(
        enabled=True,
        strategy="short",
        skip_enhanced=False,
        skip_ai=True,
        cron_hour=21,
        cron_minute=0,
        timezone="Asia/Shanghai",
        auto_catch_up=True,
        misfire_grace_minutes=60,
    )
    scheduler = FakeScheduler()
    monkeypatch.setattr(daily_job, "datetime", FixedDatetime)
    monkeypatch.setattr(daily_job, "get_session", fake_session)
    monkeypatch.setattr(daily_job.repo, "is_trade_date", lambda session, trade_date: True)
    monkeypatch.setattr(daily_job, "has_successful_scheduler_run", lambda *args, **kwargs: False)
    monkeypatch.setattr(daily_job, "has_selection_result", lambda *args, **kwargs: False)

    daily_job._register_catch_up_job_if_needed(scheduler, runtime_cfg)

    assert len(scheduler.jobs) == 1
    assert scheduler.jobs[0]["kwargs"]["id"] == "daily_stock_selection_catch_up"
    assert scheduler.jobs[0]["kwargs"]["kwargs"]["trade_date"] == date(2026, 5, 11)
