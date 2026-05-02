"""任务执行与调度管理 API。"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from loguru import logger

from mo_stock.web.schemas import (
    RunTaskRequest,
    SchedulerConfig,
    SchedulerStatusResponse,
    TaskStatusResponse,
)

router = APIRouter(tags=["tasks"])

CN_TZ = ZoneInfo("Asia/Shanghai")

# ---------------------------------------------------------------------------
# run-once 状态管理
# ---------------------------------------------------------------------------

_task_lock = threading.Lock()
_task_state: dict = {
    "task_id": None,
    "status": "idle",  # idle / running / error
    "strategy": None,
    "trade_date": None,
    "started_at": None,
    "error": None,
}


def _run_in_background(task_id: str, strategy: str, trade_date: date | None,
                       skip_ai: bool, force: bool) -> None:
    """后台线程执行 run_daily_pipeline。"""
    from mo_stock.scheduler.daily_job import run_daily_pipeline

    try:
        run_daily_pipeline(
            trade_date=trade_date,
            skip_ai=skip_ai,
            strategy=strategy,
        )
    except Exception as exc:
        logger.exception("后台选股任务失败 task_id={}", task_id)
        with _task_lock:
            _task_state["status"] = "error"
            _task_state["error"] = str(exc)
        return

    with _task_lock:
        _task_state["status"] = "idle"
        _task_state["task_id"] = None
        _task_state["error"] = None


@router.post("/tasks/run", response_model=TaskStatusResponse)
async def run_task(req: RunTaskRequest) -> TaskStatusResponse:
    """触发一次选股任务。"""
    if req.strategy not in ("short", "swing"):
        raise HTTPException(status_code=400, detail=f"非法 strategy: {req.strategy}")

    trade_date = None
    if req.trade_date:
        try:
            trade_date = date.fromisoformat(req.trade_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="trade_date 格式错误，需 YYYY-MM-DD") from None

    with _task_lock:
        if _task_state["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中，请等待完成")

        task_id = uuid.uuid4().hex[:8]
        _task_state.update({
            "task_id": task_id,
            "status": "running",
            "strategy": req.strategy,
            "trade_date": req.trade_date or str(date.today()),
            "started_at": datetime.now(CN_TZ).isoformat(),
            "error": None,
        })

    t = threading.Thread(
        target=_run_in_background,
        args=(task_id, req.strategy, trade_date, req.skip_ai, req.force),
        daemon=True,
    )
    t.start()

    return TaskStatusResponse(**_task_state)


@router.get("/tasks/status", response_model=TaskStatusResponse)
async def get_task_status() -> TaskStatusResponse:
    return TaskStatusResponse(**_task_state)


# ---------------------------------------------------------------------------
# scheduler 状态管理
# ---------------------------------------------------------------------------

_sched_lock = threading.Lock()
_sched_state: dict = {
    "status": "stopped",
    "scheduler": None,
    "strategy": None,
    "cron": None,
}


def _start_scheduler_thread(config: SchedulerConfig) -> None:
    """在后台线程启动 BlockingScheduler。"""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    from mo_stock.scheduler.daily_job import run_daily_pipeline

    sched = BlockingScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        run_daily_pipeline,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=config.cron_hour,
            minute=config.cron_minute,
        ),
        id="daily_stock_selection",
        name="每日选股流程",
        misfire_grace_time=3600,
        kwargs={"skip_ai": config.skip_ai, "strategy": config.strategy},
    )

    with _sched_lock:
        _sched_state["scheduler"] = sched

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        with _sched_lock:
            _sched_state["status"] = "stopped"
            _sched_state["scheduler"] = None


@router.post("/scheduler/start")
async def start_scheduler(config: SchedulerConfig) -> dict:
    """启动定时调度器。"""
    if config.strategy not in ("short", "swing"):
        raise HTTPException(status_code=400, detail=f"非法 strategy: {config.strategy}")

    with _sched_lock:
        if _sched_state["status"] == "running":
            raise HTTPException(status_code=409, detail="调度器已在运行中")

        cron_expr = f"{config.cron_minute} {config.cron_hour} * * mon-fri"
        _sched_state.update({
            "status": "running",
            "strategy": config.strategy,
            "cron": cron_expr,
        })

    t = threading.Thread(
        target=_start_scheduler_thread,
        args=(config,),
        daemon=True,
    )
    t.start()

    return {"message": "调度器已启动", "cron": cron_expr}


@router.post("/scheduler/stop")
async def stop_scheduler() -> dict:
    """停止定时调度器。"""
    with _sched_lock:
        if _sched_state["status"] != "running" or _sched_state["scheduler"] is None:
            raise HTTPException(status_code=409, detail="调度器未在运行")

        _sched_state["scheduler"].shutdown(wait=False)
        _sched_state["status"] = "stopped"
        _sched_state["scheduler"] = None
        _sched_state["cron"] = None

    return {"message": "调度器已停止"}


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status() -> SchedulerStatusResponse:
    with _sched_lock:
        status = _sched_state["status"]
        strategy = _sched_state.get("strategy")
        cron = _sched_state.get("cron")

        next_run = None
        if status == "running" and _sched_state.get("scheduler"):
            try:
                job = _sched_state["scheduler"].get_jobs()[0]
                next_run = str(job.next_run_time) if job.next_run_time else None
            except Exception:
                pass

    return SchedulerStatusResponse(
        status=status,
        strategy=strategy,
        cron=cron,
        next_run=next_run,
    )
