"""APScheduler 调度入口：每交易日 15:30 执行 run-once。

用法：
    from mo_stock.scheduler.daily_job import start_scheduler
    start_scheduler()   # 阻塞运行

或通过 CLI：
    mo-stock scheduler
"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.short.lhb_filter import LhbFilter
from mo_stock.filters.short.limit_filter import LimitFilter
from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter
from mo_stock.filters.short.sector_filter import SectorFilter
from mo_stock.filters.short.theme_filter import ThemeFilter
from mo_stock.filters.swing.catalyst_filter import CatalystFilter
from mo_stock.filters.swing.market_regime_filter import MarketRegimeFilter
from mo_stock.filters.swing.moneyflow_swing_filter import MoneyflowSwingFilter
from mo_stock.filters.swing.pullback_filter import PullbackFilter
from mo_stock.filters.swing.risk_liquidity_filter import RiskLiquidityFilter
from mo_stock.filters.swing.sector_swing_filter import SectorSwingFilter
from mo_stock.filters.swing.theme_swing_filter import ThemeSwingFilter
from mo_stock.filters.swing.trend_filter import TrendFilter
from mo_stock.ingest.ingest_daily import DailyIngestor
from mo_stock.report.render_md import render_daily_report
from mo_stock.scorer.combine import combine_scores, replace_filter_scores
from mo_stock.storage.db import get_session

# A 股交易时区，所有时点判断都基于此（UTC+8）
CN_TZ = ZoneInfo("Asia/Shanghai")

# 龙虎榜数据由交易所于 T 日盘后（约 15:30 之后）发布，调度必须晚于此刻
# 否则 LhbFilter 会用"还没出"的数据 → look-ahead bias。
LHB_AVAILABLE_AFTER = time(15, 30)


def _assert_lhb_data_available(trade_date: date, now: datetime | None = None) -> None:
    """断言：当前已晚于 T 日 15:30，龙虎榜数据可用。

    防御 P0-1：若运维误把调度提前到盘中（如 14:00），会用未来的龙虎榜信号
    打分，等价于"用未来数据选当前股票"——量化里的 look-ahead bias。

    注意：仅当 trade_date == 当日 时才校验；回填历史日期不会触发。
    """
    now = now or datetime.now(CN_TZ)
    today = now.date()
    if trade_date != today:
        return  # 历史回填，数据早已可用
    if now.time() < LHB_AVAILABLE_AFTER:
        raise RuntimeError(
            f"调度时点 {now.strftime('%H:%M:%S')} 早于龙虎榜发布时间 15:30；"
            f"提前选股会使用未来数据，已阻止运行。请晚于 15:30 后再触发。"
        )


def run_daily_pipeline(
    trade_date: date | None = None, *,
    skip_enhanced: bool = False,
    skip_ai: bool = False,
    strategy: str = "short",
) -> None:
    """v2.2 端到端流程（与 cli.run_once 内部一致）。

    P1-18：顶层 try-except 捕获并 logger.exception 记录完整堆栈，避免 APScheduler
    默认行为吞掉异常导致排查困难。

    Args:
        trade_date: 目标交易日，None 取当天
        skip_enhanced: True 时只跑 CORE ingest 步骤
        skip_ai: True 时跳过 combine_scores 的 AI 阶段，行为等同 v2.1
        strategy: 策略标识，支持 short / swing
    """
    from config.settings import settings

    trade_date = trade_date or date.today()
    strategy = _validate_strategy(strategy)
    if strategy == "swing" and not skip_ai:
        logger.warning("swing AI prompt 尚未接入（Phase 4），scheduler 本次自动跳过 AI")
        skip_ai = True
    logger.info(
        "===== 每日定时任务触发：{} strategy={} (skip_enhanced={} skip_ai={}) =====",
        trade_date, strategy, skip_enhanced, skip_ai,
    )

    try:
        # P0-1：龙虎榜时点保护——选当日时必须晚于 15:30
        _assert_lhb_data_available(trade_date)

        DailyIngestor().ingest_one_day(trade_date, skip_enhanced=skip_enhanced)

        cfg = load_weights_yaml(_weights_path_for_strategy(strategy))
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


def start_scheduler(
    *,
    skip_enhanced: bool = False,
    skip_ai: bool = False,
    strategy: str = "short",
) -> None:
    """启动阻塞调度器。周一至周五 15:30 (Asia/Shanghai) 触发。

    Args:
        skip_enhanced: 透传给每日任务的 ingest_one_day（True 时只跑 CORE 步骤）
        skip_ai: 透传给 run_daily_pipeline，跳过 AI 分析
        strategy: 透传给 run_daily_pipeline，支持 short / swing
    """
    strategy = _validate_strategy(strategy)
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
        # P1-18：错过 60 分钟内仍补跑（原 30 分钟偏短，遇网络抖动易错过窗口）
        misfire_grace_time=60 * 60,
        kwargs={"skip_enhanced": skip_enhanced, "skip_ai": skip_ai, "strategy": strategy},
    )

    logger.info(
        "scheduler 已启动：每交易日 15:30 触发 (strategy={} skip_enhanced={} skip_ai={})",
        strategy, skip_enhanced, skip_ai,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler 被中断，退出")


def _validate_strategy(strategy: str) -> str:
    normalized = strategy.strip().lower()
    if normalized not in {"short", "swing"}:
        raise ValueError("strategy 仅支持 short / swing")
    return normalized


def _weights_path_for_strategy(strategy: str) -> Path:
    root = Path(__file__).resolve().parent.parent.parent.parent
    filename = "weights.yaml" if strategy == "short" else "weights_swing.yaml"
    return root / "config" / filename


def _build_filters(strategy: str, cfg: dict):
    if strategy == "short":
        return [
            LimitFilter(weights=cfg.get("limit_filter", {})),
            MoneyflowFilter(weights=cfg.get("moneyflow_filter", {})),
            LhbFilter(weights=cfg.get("lhb_filter", {})),
            SectorFilter(weights=cfg.get("sector_filter", {})),
            ThemeFilter(weights=cfg.get("theme_filter", {})),
        ], ["limit", "moneyflow", "lhb", "sector", "theme"]

    return [
        TrendFilter(weights=cfg.get("trend_filter", {})),
        PullbackFilter(weights=cfg.get("pullback_filter", {})),
        MoneyflowSwingFilter(weights=cfg.get("moneyflow_swing_filter", {})),
        SectorSwingFilter(weights=cfg.get("sector_swing_filter", {})),
        ThemeSwingFilter(weights=cfg.get("theme_swing_filter", {})),
        CatalystFilter(weights=cfg.get("catalyst_filter", {})),
        RiskLiquidityFilter(weights=cfg.get("risk_liquidity_filter", {})),
    ], [
        "trend", "pullback", "moneyflow_swing", "sector_swing",
        "theme_swing", "catalyst", "risk_liquidity",
    ]
