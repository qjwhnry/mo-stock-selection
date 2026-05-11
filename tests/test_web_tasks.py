"""任务执行与调度 API 测试。"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mo_stock.web.app import app


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试前重置 tasks/scheduler 状态。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._task_lock:
        tasks_mod._task_state.clear()
        tasks_mod._task_state.update(tasks_mod._TASK_INITIAL_STATE)
    with tasks_mod._sched_lock:
        if tasks_mod._sched_state["scheduler"]:
            tasks_mod._sched_state["scheduler"].shutdown(wait=False)
        tasks_mod._sched_state.clear()
        tasks_mod._sched_state.update({
            "status": "stopped", "scheduler": None,
            "strategy": None, "cron": None,
            "enabled": None, "skip_enhanced": None,
            "skip_ai": None, "timezone": None,
            "auto_catch_up": None,
        })
    yield


@pytest.fixture()
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

class TestRunTaskValidation:
    def test_invalid_strategy(self, client):
        resp = client.post("/api/tasks/run", json={"strategy": "invalid"})
        assert resp.status_code == 400

    def test_invalid_trade_date(self, client):
        resp = client.post("/api/tasks/run", json={"trade_date": "not-a-date"})
        assert resp.status_code == 400


class TestDataTaskValidation:
    def test_backfill_days_too_large(self, client):
        resp = client.post("/api/tasks/backfill", json={"days": 731})
        assert resp.status_code == 422

    def test_refresh_index_days_too_large(self, client):
        resp = client.post("/api/tasks/refresh-index", json={"days": 1826})
        assert resp.status_code == 422

    def test_refresh_metadata_empty_options_rejected(self, client):
        resp = client.post("/api/tasks/refresh-metadata", json={
            "refresh_basics": False,
            "with_ths": False,
            "with_hm_list": False,
            "refresh_calendar": False,
        })
        assert resp.status_code == 422

    def test_refresh_metadata_calendar_requires_start(self, client):
        resp = client.post("/api/tasks/refresh-metadata", json={"refresh_calendar": True})
        assert resp.status_code == 422


class TestSchedulerValidation:
    def test_invalid_strategy(self, client):
        resp = client.post("/api/scheduler/start", json={"strategy": "bad"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 并发 / 重复操作拒绝
# ---------------------------------------------------------------------------

def test_concurrent_run_rejected(client):
    """模拟运行中时拒绝第二次提交。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._task_lock:
        tasks_mod._task_state["status"] = "running"

    resp = client.post("/api/tasks/run", json={"strategy": "short"})
    assert resp.status_code == 409


def test_double_scheduler_start_rejected(client):
    """调度器已在运行时拒绝再次启动。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._sched_lock:
        tasks_mod._sched_state["status"] = "running"

    resp = client.post("/api/scheduler/start", json={"strategy": "short"})
    assert resp.status_code == 409


def test_stop_not_running(client):
    """停止未运行的调度器返回 409。"""
    resp = client.post("/api/scheduler/stop")
    assert resp.status_code == 409


def test_scheduler_start_success_persists_config(client):
    """启动调度器时保存数据库配置，并返回 cron 表达式。"""
    runtime_cfg = SimpleNamespace(
        enabled=True,
        strategy="swing",
        skip_enhanced=True,
        skip_ai=True,
        cron_hour=15,
        cron_minute=40,
        timezone="Asia/Shanghai",
        auto_catch_up=True,
        misfire_grace_minutes=60,
    )
    with (
        patch("mo_stock.scheduler.state.save_runtime_config", return_value=runtime_cfg) as save_config,
        patch("mo_stock.web.routers.tasks._start_scheduler_thread"),
    ):
        resp = client.post("/api/scheduler/start", json={
            "strategy": "swing",
            "skip_enhanced": True,
            "skip_ai": True,
            "cron_hour": 15,
            "cron_minute": 40,
        })

    assert resp.status_code == 200
    assert resp.json()["cron"] == "40 15 * * mon-fri"
    assert save_config.call_args.kwargs["enabled"] is True
    assert save_config.call_args.kwargs["skip_enhanced"] is True


def test_scheduler_start_save_failure_resets_placeholder(client):
    """保存配置失败时释放 running 占位，避免后续无法启动。"""
    from mo_stock.web.routers import tasks as tasks_mod

    with patch("mo_stock.scheduler.state.save_runtime_config", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError, match="db down"):
            client.post("/api/scheduler/start", json={"strategy": "short"})

    with tasks_mod._sched_lock:
        assert tasks_mod._sched_state["status"] == "stopped"
        assert tasks_mod._sched_state["scheduler"] is None


def test_scheduler_start_invalid_timezone(client):
    resp = client.post("/api/scheduler/start", json={"timezone": "Bad/Zone"})
    assert resp.status_code == 400


def test_autostart_scheduler_from_db_sets_web_state():
    """Web 启动恢复 enabled 配置时，应设置进程内运行状态。"""
    from mo_stock.scheduler.state import SchedulerRuntimeConfig
    from mo_stock.web.routers import tasks as tasks_mod

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
    with (
        patch("mo_stock.scheduler.daily_job.load_enabled_runtime_config", return_value=runtime_cfg),
        patch("mo_stock.web.routers.tasks._start_scheduler_thread"),
    ):
        tasks_mod.autostart_scheduler_from_db()

    with tasks_mod._sched_lock:
        assert tasks_mod._sched_state["status"] == "running"
        assert tasks_mod._sched_state["cron"] == "0 21 * * mon-fri"
        assert tasks_mod._sched_state["timezone"] == "Asia/Shanghai"


def test_scheduler_stop_persists_disabled(client):
    """停止调度器时同步关闭数据库 enabled。"""
    from mo_stock.web.routers import tasks as tasks_mod

    fake_scheduler = SimpleNamespace(shutdown=lambda wait: None)
    with tasks_mod._sched_lock:
        tasks_mod._sched_state.update({
            "status": "running",
            "scheduler": fake_scheduler,
            "strategy": "short",
            "cron": "30 15 * * mon-fri",
        })

    with patch("mo_stock.scheduler.state.save_runtime_config") as save_config:
        resp = client.post("/api/scheduler/stop")

    assert resp.status_code == 200
    assert save_config.call_args.kwargs == {"enabled": False}


# ---------------------------------------------------------------------------
# 正常流程
# ---------------------------------------------------------------------------

def test_run_task_success(client):
    """触发选股成功，返回 task_id。"""
    with (
        patch("mo_stock.web.routers.tasks._ensure_trade_date"),
        patch("mo_stock.web.routers.tasks._execute_background_task"),
    ):
        resp = client.post("/api/tasks/run", json={"strategy": "short"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_type"] == "run_pipeline"
    assert body["task_id"] is not None
    assert body["stats"] == {}


def test_run_task_force_passed_to_trade_date_check(client):
    """force 参数必须透传到交易日校验，避免 schema 字段无效。"""
    with (
        patch("mo_stock.web.routers.tasks._ensure_trade_date") as ensure_trade_date,
        patch("mo_stock.web.routers.tasks._execute_background_task"),
    ):
        resp = client.post("/api/tasks/run", json={
            "strategy": "short",
            "trade_date": "2026-05-05",
            "force": True,
        })
    assert resp.status_code == 200
    assert ensure_trade_date.call_args.kwargs["force"] is True


def test_task_status_idle(client):
    resp = client.get("/api/tasks/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "idle"
    assert body["task_type"] is None
    assert body["stats"] == {}


def test_sync_one_day_task_success(client):
    with patch("mo_stock.web.routers.tasks._execute_background_task"):
        resp = client.post("/api/tasks/sync-one-day", json={"trade_date": "2026-05-05"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_type"] == "sync_one_day"
    assert body["trade_date"] == "2026-05-05"


def test_backfill_task_success(client):
    with patch("mo_stock.web.routers.tasks._execute_background_task"):
        resp = client.post("/api/tasks/backfill", json={"days": 10, "end_date": "2026-05-05"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_type"] == "backfill"
    assert "最近 10 天" in body["message"]


def test_refresh_index_task_success(client):
    with patch("mo_stock.web.routers.tasks._execute_background_task"):
        resp = client.post("/api/tasks/refresh-index", json={"days": 10, "end_date": "2026-05-05"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_type"] == "refresh_index"


def test_refresh_metadata_task_success(client):
    with patch("mo_stock.web.routers.tasks._execute_background_task"):
        resp = client.post("/api/tasks/refresh-metadata", json={"refresh_basics": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_type"] == "refresh_metadata"


def test_execute_background_task_success_writes_stats():
    """后台任务成功后写入 success / stats。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._task_lock:
        tasks_mod._task_state.update({
            "task_id": "abc123",
            "task_type": "sync_one_day",
            "status": "running",
            "stats": {},
        })

    tasks_mod._execute_background_task("abc123", lambda: {"daily_kline": 10}, "同步完成")

    with tasks_mod._task_lock:
        assert tasks_mod._task_state["status"] == "success"
        assert tasks_mod._task_state["message"] == "同步完成"
        assert tasks_mod._task_state["stats"] == {"daily_kline": 10}


def test_execute_background_task_error_writes_error():
    """后台任务失败后写入 error。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._task_lock:
        tasks_mod._task_state.update({
            "task_id": "abc123",
            "task_type": "sync_one_day",
            "status": "running",
            "stats": {},
        })

    def _boom():
        raise RuntimeError("boom")

    tasks_mod._execute_background_task("abc123", _boom, "不会成功")

    with tasks_mod._task_lock:
        assert tasks_mod._task_state["status"] == "error"
        assert "boom" in tasks_mod._task_state["error"]


def test_scheduler_status_stopped(client):
    resp = client.get("/api/scheduler/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"
