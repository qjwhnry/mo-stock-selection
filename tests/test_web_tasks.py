"""任务执行与调度 API 测试。"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mo_stock.web.app import app


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试前重置 tasks/scheduler 状态。"""
    from mo_stock.web.routers import tasks as tasks_mod
    with tasks_mod._task_lock:
        tasks_mod._task_state.update({
            "task_id": None, "status": "idle", "strategy": None,
            "trade_date": None, "started_at": None, "error": None,
        })
    with tasks_mod._sched_lock:
        if tasks_mod._sched_state["scheduler"]:
            tasks_mod._sched_state["scheduler"].shutdown(wait=False)
        tasks_mod._sched_state.update({
            "status": "stopped", "scheduler": None,
            "strategy": None, "cron": None,
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


# ---------------------------------------------------------------------------
# 正常流程
# ---------------------------------------------------------------------------

def test_run_task_success(client):
    """触发选股成功，返回 task_id。"""
    with patch("mo_stock.web.routers.tasks._run_in_background"):
        resp = client.post("/api/tasks/run", json={"strategy": "short"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_id"] is not None


def test_task_status_idle(client):
    resp = client.get("/api/tasks/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


def test_scheduler_status_stopped(client):
    resp = client.get("/api/scheduler/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"
