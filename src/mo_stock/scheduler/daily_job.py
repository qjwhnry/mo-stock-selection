"""APScheduler 调度入口：每交易日 15:30 执行 run-once。

用法：
    from mo_stock.scheduler.daily_job import start_scheduler
    start_scheduler()   # 阻塞运行

或通过 CLI：
    mo-stock scheduler
"""
from __future__ import annotations

from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.limit_filter import LimitFilter
from mo_stock.filters.moneyflow_filter import MoneyflowFilter
from mo_stock.ingest.ingest_daily import DailyIngestor
from mo_stock.report.render_md import render_daily_report
from mo_stock.scorer.combine import combine_scores, persist_filter_scores
from mo_stock.storage.db import get_session


def run_daily_pipeline(trade_date: date | None = None) -> None:
    """Phase 1 端到端流程（与 cli.run_once 内部一致）。"""
    from pathlib import Path

    from config.settings import settings

    trade_date = trade_date or date.today()
    logger.info("===== 每日定时任务触发：{} =====", trade_date)

    DailyIngestor().ingest_one_day(trade_date)

    weights_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "weights.yaml"
    cfg = load_weights_yaml(weights_path)
    dim_weights = cfg.get("dimension_weights", {})
    hard_reject = cfg.get("hard_reject", {})

    limit_filter = LimitFilter(weights=cfg.get("limit_filter", {}))
    mf_filter = MoneyflowFilter(weights=cfg.get("moneyflow_filter", {}))

    with get_session() as session:
        scores = limit_filter.score_all(session, trade_date)
        scores += mf_filter.score_all(session, trade_date)
        persist_filter_scores(session, scores)

        combine_scores(
            session,
            trade_date,
            dimension_weights=dim_weights,
            hard_reject_cfg=hard_reject,
            top_n=settings.top_n_final,
        )

    with get_session() as session:
        render_daily_report(session, trade_date, output_dir=settings.report_dir)

    logger.info("===== 每日任务完成：{} =====", trade_date)


def start_scheduler() -> None:
    """启动阻塞调度器。周一至周五 15:30 (Asia/Shanghai) 触发。"""
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(
        run_daily_pipeline,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=30,
        ),
        id="daily_stock_selection",
        name="每日 15:30 A 股选股流程",
        misfire_grace_time=60 * 30,  # 错过 30 分钟内仍补跑
    )

    logger.info("scheduler 已启动：每交易日 15:30 触发")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler 被中断，退出")
