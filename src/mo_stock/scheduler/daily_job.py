"""APScheduler 调度入口：每交易日默认 21:00 执行 run-once。

用法：
    from mo_stock.scheduler.daily_job import start_scheduler
    start_scheduler()   # 阻塞运行

或通过 CLI：
    mo-stock scheduler
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.swing.market_regime_filter import MarketRegimeFilter
from mo_stock.ingest.ingest_daily import DailyIngestor
from mo_stock.pipeline import CN_TZ, assert_lhb_data_available, build_filters
from mo_stock.report.render_md import render_daily_report
from mo_stock.scheduler.state import (
    SchedulerRuntimeConfig,
    finish_run,
    has_selection_result,
    has_successful_scheduler_run,
    load_runtime_config,
    mark_stale_running_runs_failed,
    save_runtime_config,
    start_run,
)
from mo_stock.scorer.combine import combine_scores, replace_filter_scores
from mo_stock.storage import repo
from mo_stock.storage.db import get_session

# 保留原私有名称，兼容已有调用与测试；实现统一由 pipeline 模块维护。
_assert_lhb_data_available = assert_lhb_data_available
_build_filters = build_filters


def run_daily_pipeline(
    trade_date: date | None = None, *,
    skip_enhanced: bool = False,
    skip_ai: bool = True,
    strategy: str = "short",
) -> None:
    """每日端到端流程（与 cli.run_once 内部一致）。

    P1-18：顶层 try-except 捕获并 logger.exception 记录完整堆栈，避免 APScheduler
    默认行为吞掉异常导致排查困难。

    Args:
        trade_date: 目标交易日，None 取当天
        skip_enhanced: True 时只跑 CORE ingest 步骤
        skip_ai: True 时跳过 combine_scores 的 AI 阶段，final_score 直接使用 rule_score
        strategy: 策略标识，支持 short / swing
    """
    from config.settings import settings

    trade_date = trade_date or date.today()
    strategy = _validate_strategy(strategy)
    logger.info(
        "===== 每日定时任务触发：{} strategy={} (skip_enhanced={} skip_ai={}) =====",
        trade_date, strategy, skip_enhanced, skip_ai,
    )

    try:
        # P0-1：龙虎榜时点保护——选当日时必须晚于 15:30
        _assert_lhb_data_available(trade_date)

        DailyIngestor().ingest_one_day(trade_date, skip_enhanced=skip_enhanced)

        cfg = load_weights_yaml(_weights_path_for_strategy(strategy))
        # short 配置作为 combine 默认值来源；strategy 配置存在时会覆盖。
        # hard_reject 同理优先使用当前 strategy，缺失时才回退到 short。
        base_cfg = load_weights_yaml(_weights_path_for_strategy("short"))
        dim_weights = cfg.get("dimension_weights", {})
        hard_reject = cfg.get("hard_reject", base_cfg.get("hard_reject", {}))
        combine_cfg = {**base_cfg.get("combine", {}), **cfg.get("combine", {})}
        if "market_regime_control" in cfg:
            combine_cfg["market_regime_control"] = cfg["market_regime_control"]
        filters, dims = _build_filters(strategy, cfg)

        with get_session() as session:
            scores = []
            for filter_obj in filters:
                scores.extend(filter_obj.score_all(session, trade_date))
            regime_score = (
                MarketRegimeFilter().score_market(session, trade_date)
                if strategy == "swing" else None
            )
            # v2.3：用 replace 而非 upsert，清掉旧维度脏分数
            replace_filter_scores(
                session, trade_date,
                dims=dims,
                results=scores,
                strategy=strategy,
            )

            combine_scores(
                session,
                trade_date,
                dimension_weights=dim_weights,
                hard_reject_cfg=hard_reject,
                top_n=settings.top_n_final,
                enable_ai=not skip_ai,
                combine_cfg=combine_cfg,
                strategy=strategy,
                regime_score=regime_score,
            )

        with get_session() as session:
            render_daily_report(
                session,
                trade_date,
                output_dir=settings.report_dir,
                strategy=strategy,
            )

        logger.info("===== 每日任务完成：{} =====", trade_date)
    except Exception:
        # 完整堆栈 → 日志文件，便于事后排查；APScheduler 不会因此停掉调度器
        logger.exception("每日任务失败 trade_date={} strategy={}", trade_date, strategy)
        # TODO(P1-18): 接入告警 webhook（钉钉 / 飞书 / Slack）
        raise


def run_daily_pipeline_with_history(
    trade_date: date | None = None, *,
    skip_enhanced: bool = False,
    skip_ai: bool = True,
    strategy: str = "short",
    source: str = "scheduled",
) -> None:
    """带执行历史记录的调度入口。

    仅 scheduler / catch-up 使用；手工 `run-once` 保持原有路径。
    """
    trade_date = trade_date or datetime.now(CN_TZ).date()
    strategy = _validate_strategy(strategy)
    run_id = start_run(trade_date=trade_date, strategy=strategy, source=source)
    try:
        run_daily_pipeline(
            trade_date=trade_date,
            skip_enhanced=skip_enhanced,
            skip_ai=skip_ai,
            strategy=strategy,
        )
    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise
    finish_run(run_id, status="success")


def start_scheduler(
    *,
    skip_enhanced: bool | None = None,
    skip_ai: bool | None = None,
    strategy: str | None = None,
    cron_hour: int | None = None,
    cron_minute: int | None = None,
) -> None:
    """启动阻塞调度器。周一至周五按数据库配置触发，默认 21:00。

    Args:
        skip_enhanced: 可选覆盖数据库配置；True 时只跑 CORE 步骤
        skip_ai: 可选覆盖数据库配置；True 时跳过 AI 分析
        strategy: 可选覆盖数据库配置；支持 short / swing
        cron_hour: 可选覆盖数据库配置；触发小时
        cron_minute: 可选覆盖数据库配置；触发分钟
    """
    if strategy is not None:
        strategy = _validate_strategy(strategy)

    stale_count = mark_stale_running_runs_failed()
    if stale_count:
        logger.warning("已清理 {} 条遗留 running 调度记录", stale_count)

    runtime_cfg = save_runtime_config(
        enabled=True,
        skip_enhanced=skip_enhanced,
        skip_ai=skip_ai,
        strategy=strategy,
        cron_hour=cron_hour,
        cron_minute=cron_minute,
    )
    scheduler = BlockingScheduler(timezone=runtime_cfg.timezone)
    register_scheduler_jobs(scheduler, runtime_cfg)

    logger.info(
        "scheduler 已启动：每交易日 {:02d}:{:02d} 触发 "
        "(strategy={} skip_enhanced={} skip_ai={} catch_up={})",
        runtime_cfg.cron_hour,
        runtime_cfg.cron_minute,
        runtime_cfg.strategy,
        runtime_cfg.skip_enhanced,
        runtime_cfg.skip_ai,
        runtime_cfg.auto_catch_up,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler 被中断，退出")


def register_scheduler_jobs(scheduler, runtime_cfg: SchedulerRuntimeConfig) -> None:
    """按数据库配置向 APScheduler 注册每日任务和可选补跑任务。"""
    scheduler.add_job(
        run_daily_pipeline_with_history,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=runtime_cfg.cron_hour,
            minute=runtime_cfg.cron_minute,
            timezone=ZoneInfo(runtime_cfg.timezone),
        ),
        id="daily_stock_selection",
        name="每日 A 股选股流程",
        misfire_grace_time=runtime_cfg.misfire_grace_minutes * 60,
        kwargs={
            "skip_enhanced": runtime_cfg.skip_enhanced,
            "skip_ai": runtime_cfg.skip_ai,
            "strategy": runtime_cfg.strategy,
            "source": "scheduled",
        },
        replace_existing=True,
    )
    _register_catch_up_job_if_needed(scheduler, runtime_cfg)


def load_enabled_runtime_config() -> SchedulerRuntimeConfig | None:
    """服务启动时读取已启用的数据库配置。"""
    stale_count = mark_stale_running_runs_failed()
    if stale_count:
        logger.warning("已清理 {} 条遗留 running 调度记录", stale_count)
    runtime_cfg = load_runtime_config()
    return runtime_cfg if runtime_cfg.enabled else None


def _register_catch_up_job_if_needed(scheduler, runtime_cfg: SchedulerRuntimeConfig) -> None:
    """启动时若错过今日调度且仍在当天，注册一次性补跑任务。"""
    if not runtime_cfg.auto_catch_up:
        return

    tz = ZoneInfo(runtime_cfg.timezone)
    now = datetime.now(tz)
    scheduled_at = datetime.combine(
        now.date(),
        time(runtime_cfg.cron_hour, runtime_cfg.cron_minute),
        tzinfo=tz,
    )
    if now < scheduled_at:
        return
    end_of_day = datetime.combine(
        now.date() + timedelta(days=1),
        time.min,
        tzinfo=tz,
    )
    if now >= end_of_day:
        return

    trade_date = now.date()
    try:
        with get_session() as session:
            if not repo.is_trade_date(session, trade_date):
                return
            if has_successful_scheduler_run(
                session, trade_date=trade_date, strategy=runtime_cfg.strategy,
            ):
                return
            if has_selection_result(
                session, trade_date=trade_date, strategy=runtime_cfg.strategy,
            ):
                return
    except Exception:
        logger.exception("检查 scheduler catch-up 条件失败，跳过自动补跑")
        return

    scheduler.add_job(
        run_daily_pipeline_with_history,
        trigger=DateTrigger(run_date=now + timedelta(seconds=1)),
        id="daily_stock_selection_catch_up",
        name="启动后补跑 A 股选股流程",
        kwargs={
            "trade_date": trade_date,
            "skip_enhanced": runtime_cfg.skip_enhanced,
            "skip_ai": runtime_cfg.skip_ai,
            "strategy": runtime_cfg.strategy,
            "source": "catch_up",
        },
        replace_existing=True,
    )
    logger.warning("检测到今日调度可能错过，已注册启动补跑任务：{}", trade_date)


def _validate_strategy(strategy: str) -> str:
    normalized = strategy.strip().lower()
    if normalized not in {"short", "swing"}:
        raise ValueError("strategy 仅支持 short / swing")
    return normalized


def _weights_path_for_strategy(strategy: str) -> Path:
    root = Path(__file__).resolve().parent.parent.parent.parent
    filename = "weights.yaml" if strategy == "short" else "weights_swing.yaml"
    return root / "config" / filename
