"""任务执行与调度管理 API。"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from loguru import logger

from mo_stock.web.schemas import (
    BackfillTaskRequest,
    RefreshIndexTaskRequest,
    RefreshMetadataTaskRequest,
    RunTaskRequest,
    SchedulerConfig,
    SchedulerStatusResponse,
    SyncOneDayTaskRequest,
    TaskStatusResponse,
)

router = APIRouter(tags=["tasks"])

CN_TZ = ZoneInfo("Asia/Shanghai")

# ---------------------------------------------------------------------------
# run-once 状态管理
# ---------------------------------------------------------------------------

_task_lock = threading.Lock()
_TASK_INITIAL_STATE: dict = {
    "task_id": None,
    "task_type": None,
    "status": "idle",  # idle / running / success / error
    "strategy": None,
    "trade_date": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "message": None,
    "stats": {},
}
_task_state: dict = dict(_TASK_INITIAL_STATE)

TaskTarget = Callable[[], dict[str, int] | None]


def _today_cn() -> date:
    """取 Asia/Shanghai 语义下的今天。"""
    return datetime.now(CN_TZ).date()


def _now_iso() -> str:
    """取 Asia/Shanghai ISO 时间字符串。"""
    return datetime.now(CN_TZ).isoformat()


def _parse_iso_date(value: str, *, field_name: str = "date") -> date:
    """解析 YYYY-MM-DD 日期，错误时返回 400。

    注意：此版本抛 HTTPException 供 FastAPI 端点使用。
    schemas.py 有同名函数抛 ValueError 供 Pydantic model_validator 使用。
    两处需保持解析逻辑一致但异常类型不同，因此刻意分离。
    """
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} 格式错误，需 YYYY-MM-DD") from None


def _state_snapshot() -> dict:
    """复制当前任务状态，避免无锁读取共享 dict。"""
    with _task_lock:
        return dict(_task_state)


def _update_progress(message: str) -> None:
    """更新运行中任务的进度消息，供前端轮询展示。"""
    with _task_lock:
        if _task_state.get("status") == "running":
            _task_state["message"] = message


def _raise_if_task_running() -> None:
    """已有任务运行时直接拒绝，避免继续做 DB 校验等额外工作。"""
    with _task_lock:
        if _task_state["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中，请等待完成")


def _validate_strategy(strategy: str) -> str:
    normalized = strategy.strip().lower()
    if normalized not in ("short", "swing"):
        raise HTTPException(status_code=400, detail=f"非法 strategy: {strategy}")
    return normalized


def _ensure_trade_date(trade_date: date, *, force: bool, kind: str) -> None:
    """校验交易日。force=True 时允许继续执行。"""
    from mo_stock.storage import repo
    from mo_stock.storage.db import get_session

    with get_session() as session:
        if repo.is_trade_date(session, trade_date):
            return

    msg = f"{trade_date.isoformat()} 不是 A 股交易日（周末 / 节假日 / 交易日历缺失）"
    if force:
        logger.warning("{}，force=true，继续运行 {}", msg, kind)
        return
    raise HTTPException(status_code=400, detail=f"{msg}；如确需运行，请传 force=true")


def _start_background_task(
    *,
    task_type: str,
    message: str,
    target: TaskTarget,
    strategy: str | None = None,
    trade_date: str | None = None,
    success_message: str | None = None,
) -> TaskStatusResponse:
    """启动通用后台任务。Phase 1 使用进程内单任务锁。"""
    with _task_lock:
        if _task_state["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中，请等待完成")

        task_id = uuid.uuid4().hex[:8]
        _task_state.update({
            "task_id": task_id,
            "task_type": task_type,
            "status": "running",
            "strategy": strategy,
            "trade_date": trade_date,
            "started_at": _now_iso(),
            "finished_at": None,
            "error": None,
            "message": message,
            "stats": {},
        })
        state = dict(_task_state)

    t = threading.Thread(
        target=_execute_background_task,
        args=(task_id, target, success_message or f"{message}完成"),
        daemon=True,
    )
    t.start()

    return TaskStatusResponse(**state)


def _execute_background_task(
    task_id: str,
    target: TaskTarget,
    success_message: str,
) -> None:
    """执行后台任务并写回最终状态。"""
    stats: dict[str, int] = {}
    try:
        result = target()
        stats = result or {}
    except Exception as exc:  # noqa: BLE001
        logger.exception("后台任务失败 task_id={}", task_id)
        with _task_lock:
            if _task_state.get("task_id") != task_id:
                return
            _task_state.update({
                "status": "error",
                "finished_at": _now_iso(),
                "error": str(exc),
                "message": "任务执行失败",
                "stats": stats,
            })
        return

    with _task_lock:
        if _task_state.get("task_id") != task_id:
            return
        _task_state.update({
            "status": "success",
            "finished_at": _now_iso(),
            "error": None,
            "message": success_message,
            "stats": stats,
        })


def _date_range_from_days(days: int, end: date | None) -> tuple[date, date]:
    """计算包含截止日在内的最近 N 个自然日窗口。"""
    end_d = end or _today_cn()
    return end_d - timedelta(days=days - 1), end_d


def _refresh_metadata(req: RefreshMetadataTaskRequest) -> dict[str, int]:
    """执行元数据刷新并统一返回 dict stats。"""
    from mo_stock.ingest.ingest_daily import DailyIngestor

    ingestor = DailyIngestor()
    stats: dict[str, int] = {}

    if req.refresh_basics:
        _update_progress("正在刷新股票基础信息")
        stats["stock_basic"] = ingestor.refresh_stock_basic()
        _update_progress("正在刷新申万行业映射")
        stats["index_member"] = ingestor.refresh_index_member()
    if req.with_ths:
        _update_progress("正在刷新同花顺概念（耗时约 3-4 分钟）")
        ths_index, ths_member = ingestor.refresh_ths_concept()
        stats["ths_index"] = ths_index
        stats["ths_member"] = ths_member
    if req.with_hm_list:
        _update_progress("正在刷新游资名录")
        stats["hot_money_list"] = ingestor.refresh_hot_money_list()
    if req.refresh_calendar:
        start = _parse_iso_date(req.calendar_start, field_name="calendar_start")
        end = (
            _parse_iso_date(req.calendar_end, field_name="calendar_end")
            if req.calendar_end else _today_cn() + timedelta(days=365)
        )
        _update_progress("正在刷新交易日历")
        stats["trade_cal"] = ingestor.refresh_trade_cal(start, end)

    return stats


def _run_pipeline_target(
    *, strategy: str, trade_date: date | None, skip_ai: bool, skip_enhanced: bool,
) -> dict[str, int]:
    """执行完整选股流程。"""
    from mo_stock.scheduler.daily_job import run_daily_pipeline

    run_daily_pipeline(
        trade_date=trade_date,
        skip_enhanced=skip_enhanced,
        skip_ai=skip_ai,
        strategy=strategy,
    )
    return {}


def _sync_one_day_target(trade_date: date, *, skip_enhanced: bool) -> dict[str, int]:
    """执行单日数据同步。"""
    from mo_stock.ingest.ingest_daily import DailyIngestor

    return DailyIngestor().ingest_one_day(
        trade_date, skip_enhanced=skip_enhanced, progress_callback=_update_progress,
    )


def _backfill_target(start: date, end: date) -> dict[str, int]:
    """执行历史回填。"""
    from mo_stock.ingest.ingest_daily import DailyIngestor

    ingestor = DailyIngestor()
    _update_progress("正在刷新基础数据和交易日历")
    stats: dict[str, int] = {
        "stock_basic": ingestor.refresh_stock_basic(),
        "trade_cal": ingestor.refresh_trade_cal(start, end + timedelta(days=30)),
    }
    stats.update(ingestor.backfill(start, end, progress_callback=_update_progress))
    return stats


def _refresh_index_target(start: date, end: date) -> dict[str, int]:
    """执行指数区间补齐。"""
    from mo_stock.ingest.ingest_daily import DailyIngestor

    return {"index_daily": DailyIngestor().refresh_index_range(
        start, end, progress_callback=_update_progress,
    )}


@router.post("/tasks/run", response_model=TaskStatusResponse)
async def run_task(req: RunTaskRequest) -> TaskStatusResponse:
    """触发一次选股任务。"""
    strategy = _validate_strategy(req.strategy)

    trade_date = None
    if req.trade_date:
        trade_date = _parse_iso_date(req.trade_date, field_name="trade_date")
    trade_date_for_validation = trade_date or _today_cn()
    _raise_if_task_running()
    _ensure_trade_date(trade_date_for_validation, force=req.force, kind="选股流程")

    trade_date_label = str(trade_date_for_validation)
    strategy_label = "短线" if strategy == "short" else "波段"

    return _start_background_task(
        task_type="run_pipeline",
        message=f"正在执行{strategy_label}选股流程 {trade_date_label}",
        target=lambda: _run_pipeline_target(
            strategy=strategy,
            trade_date=trade_date,
            skip_ai=req.skip_ai,
            skip_enhanced=req.skip_enhanced,
        ),
        strategy=strategy,
        trade_date=trade_date_label,
        success_message=f"{strategy_label}选股流程完成 {trade_date_label}",
    )


@router.post("/tasks/sync-one-day", response_model=TaskStatusResponse)
async def sync_one_day(req: SyncOneDayTaskRequest) -> TaskStatusResponse:
    """触发指定日期的单日数据同步。"""
    trade_date = _parse_iso_date(req.trade_date, field_name="trade_date") if req.trade_date else _today_cn()

    return _start_background_task(
        task_type="sync_one_day",
        message=f"正在同步 {trade_date} 日频数据",
        target=lambda: _sync_one_day_target(trade_date, skip_enhanced=req.skip_enhanced),
        trade_date=str(trade_date),
        success_message=f"{trade_date} 日频数据同步完成",
    )


@router.post("/tasks/backfill", response_model=TaskStatusResponse)
async def backfill_task(req: BackfillTaskRequest) -> TaskStatusResponse:
    """触发历史日频数据回填。"""
    end = _parse_iso_date(req.end_date, field_name="end_date") if req.end_date else _today_cn()
    start, end = _date_range_from_days(req.days, end)

    return _start_background_task(
        task_type="backfill",
        message=f"正在回填最近 {req.days} 天数据（{start} 至 {end}）",
        target=lambda: _backfill_target(start, end),
        success_message=f"最近 {req.days} 天数据回填完成（{start} 至 {end}）",
    )


@router.post("/tasks/refresh-index", response_model=TaskStatusResponse)
async def refresh_index_task(req: RefreshIndexTaskRequest) -> TaskStatusResponse:
    """触发指数日线区间补齐。"""
    end = _parse_iso_date(req.end_date, field_name="end_date") if req.end_date else _today_cn()
    start, end = _date_range_from_days(req.days, end)

    return _start_background_task(
        task_type="refresh_index",
        message=f"正在补齐最近 {req.days} 天指数日线（{start} 至 {end}）",
        target=lambda: _refresh_index_target(start, end),
        success_message=f"最近 {req.days} 天指数日线补齐完成（{start} 至 {end}）",
    )


@router.post("/tasks/refresh-metadata", response_model=TaskStatusResponse)
async def refresh_metadata_task(req: RefreshMetadataTaskRequest) -> TaskStatusResponse:
    """触发基础元数据刷新。"""
    return _start_background_task(
        task_type="refresh_metadata",
        message="正在刷新元数据",
        target=lambda: _refresh_metadata(req),
        success_message="元数据刷新完成",
    )


@router.get("/tasks/status", response_model=TaskStatusResponse)
async def get_task_status() -> TaskStatusResponse:
    return TaskStatusResponse(**_state_snapshot())


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
