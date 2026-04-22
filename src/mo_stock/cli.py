"""mo-stock 命令行入口。

用法：
    mo-stock init-db                            # 初始化 schema（简化版）
    mo-stock refresh-basics                     # 刷新 stock_basic
    mo-stock refresh-cal --start 2024-01-01     # 刷新交易日历
    mo-stock backfill --days 180                # 回填历史
    mo-stock run-once --date 2026-04-22         # 跑一次选股
    mo-stock scheduler                          # 启动常驻调度
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import click
from loguru import logger

from config.settings import settings
from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.limit_filter import LimitFilter
from mo_stock.filters.moneyflow_filter import MoneyflowFilter
from mo_stock.ingest.ingest_daily import DailyIngestor
from mo_stock.report.render_md import render_daily_report
from mo_stock.scorer.combine import combine_scores, persist_filter_scores
from mo_stock.storage.db import engine, get_session
from mo_stock.storage.models import Base

# ------------------------------------------------------------------
# 日志初始化
# ------------------------------------------------------------------

def _setup_logging() -> None:
    """配置 loguru，同时输出到控制台和 logs/YYYY-MM-DD.log。"""
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, enqueue=False)

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.log_dir / f"{date.today().isoformat()}.log"
    logger.add(
        log_path,
        level="DEBUG",
        rotation="50 MB",
        retention="30 days",
        encoding="utf-8",
    )


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ------------------------------------------------------------------
# Click 命令组
# ------------------------------------------------------------------

@click.group()
def cli() -> None:
    """mo-stock-selection 命令行。"""
    _setup_logging()


@cli.command("init-db")
@click.option("--drop", is_flag=True, help="先 DROP 再 CREATE（危险：会清空数据）")
def init_db(drop: bool) -> None:
    """用 SQLAlchemy 的 create_all() 一键建表（适合首次部署 / 开发环境）。

    生产环境推荐用 `alembic upgrade head`。
    """
    logger.info("init-db: DB_URL={}", settings.db_url)
    if drop:
        click.confirm("确认要 DROP 所有表吗？数据将丢失！", abort=True)
        Base.metadata.drop_all(engine)
        logger.warning("所有表已删除")
    Base.metadata.create_all(engine)
    logger.info("init-db 完成：{} 张表已就绪", len(Base.metadata.tables))


@cli.command("refresh-basics")
def refresh_basics() -> None:
    """刷新 stock_basic 表。"""
    DailyIngestor().refresh_stock_basic()


@cli.command("refresh-cal")
@click.option("--start", required=True, help="起始日期 YYYY-MM-DD")
@click.option("--end", default=None, help="结束日期 YYYY-MM-DD，默认今日+365 日")
def refresh_cal(start: str, end: str | None) -> None:
    """刷新交易日历。"""
    start_d = _parse_date(start)
    end_d = _parse_date(end) if end else date.today() + timedelta(days=365)
    DailyIngestor().refresh_trade_cal(start_d, end_d)


@cli.command("backfill")
@click.option("--days", default=180, show_default=True, type=int, help="回填多少天")
@click.option("--end", default=None, help="回填截止日 YYYY-MM-DD，默认今日")
def backfill(days: int, end: str | None) -> None:
    """一次性回填 [end-days, end] 的历史数据。"""
    end_d = _parse_date(end) if end else date.today()
    start_d = end_d - timedelta(days=days)

    logger.info("backfill 开始 {} → {} (共 {} 天)", start_d, end_d, days)
    # 先确保基础表就绪
    ingestor = DailyIngestor()
    ingestor.refresh_stock_basic()
    ingestor.refresh_trade_cal(start_d, end_d + timedelta(days=30))

    # 再按日回填日频数据
    stats = ingestor.backfill(start_d, end_d)
    logger.info("backfill 完成：{}", stats)


@cli.command("run-once")
@click.option("--date", "date_str", default=None, help="选股日 YYYY-MM-DD，默认今日")
@click.option("--skip-ingest", is_flag=True, help="跳过数据拉取步骤（用于已经有数据时的重算）")
def run_once(date_str: str | None, skip_ingest: bool) -> None:
    """对指定交易日跑一次端到端选股流程：ingest → filter → combine → report。"""
    trade_date = _parse_date(date_str) if date_str else date.today()
    logger.info("=== run-once {} ===", trade_date)

    # ---------- 1. 数据拉取 ----------
    if not skip_ingest:
        DailyIngestor().ingest_one_day(trade_date)

    # ---------- 2. 加载权重配置 ----------
    weights_path = Path(__file__).resolve().parent.parent.parent / "config" / "weights.yaml"
    cfg = load_weights_yaml(weights_path)
    dim_weights: dict[str, float] = cfg.get("dimension_weights", {})
    hard_reject: dict = cfg.get("hard_reject", {})

    # ---------- 3. 规则层打分（MVP：limit + moneyflow）----------
    limit_filter = LimitFilter(weights=cfg.get("limit_filter", {}))
    mf_filter = MoneyflowFilter(weights=cfg.get("moneyflow_filter", {}))

    with get_session() as session:
        limit_scores = limit_filter.score_all(session, trade_date)
        mf_scores = mf_filter.score_all(session, trade_date)
        all_scores = [*limit_scores, *mf_scores]

        persist_filter_scores(session, all_scores)

        # ---------- 4. 综合打分 + 硬规则 ----------
        combine_scores(
            session,
            trade_date,
            dimension_weights=dim_weights,
            hard_reject_cfg=hard_reject,
            top_n=settings.top_n_final,
        )

    # ---------- 5. 生成报告 ----------
    with get_session() as session:
        md_path, json_path = render_daily_report(
            session,
            trade_date,
            output_dir=settings.report_dir,
            phase="Phase 1 MVP (limit + moneyflow)",
        )

    click.echo(f"✓ 报告已生成：\n  {md_path}\n  {json_path}")


@cli.command("scheduler")
def scheduler() -> None:
    """启动常驻调度：每个交易日 15:30 自动跑 run-once。"""
    from mo_stock.scheduler.daily_job import start_scheduler

    start_scheduler()


def main() -> None:
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
