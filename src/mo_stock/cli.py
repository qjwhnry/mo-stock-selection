"""mo-stock 命令行入口。

用法（按推荐运行频率排序）：
    # ---------- 一次性 / 极低频 ----------
    mo-stock init-db                            # 首次部署：建表
    mo-stock refresh-cal --start 2024-01-01     # 每年末：刷新交易日历

    # ---------- 周度元数据 ----------
    mo-stock refresh-basics                     # 每周一：stock_basic + index_member（申万行业映射）
    mo-stock refresh-basics --with-ths          # 每月：额外刷 ths_index + ths_member（同花顺概念，需 6000 积分）

    # ---------- 一次性回填 ----------
    mo-stock backfill --days 180                # 首次部署：回填 180 天历史日频数据

    # ---------- 每日运行 ----------
    mo-stock run-once --date 2026-04-22         # 每个交易日 15:30：选股端到端
    mo-stock scheduler                          # 生产常驻：自动按交易日触发 run-once

    # ---------- 按需复盘 / 调试 ----------
    mo-stock analyze 600519.SH --date 2026-04-22 # 单股分析（不写库）

详细参数 / 运行频率速查 / 典型工作流见 docs/cli.md。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import click
from loguru import logger

from config.settings import settings
from mo_stock.analyzer import analyze_stock
from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.lhb_filter import LhbFilter
from mo_stock.filters.limit_filter import LimitFilter
from mo_stock.filters.moneyflow_filter import MoneyflowFilter
from mo_stock.filters.sector_filter import SectorFilter
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
    """CLI 日期解析：严格 'YYYY-MM-DD'，非法格式抛 click.BadParameter。

    P2-21：用 click.BadParameter 替代裸 ValueError，CLI 层会优雅显示而不是栈追踪。
    """
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise click.BadParameter(
            f"日期格式错误：{s!r}（期望 YYYY-MM-DD，例如 2026-04-26）"
        ) from exc


def _ensure_trade_date(d: date, *, force: bool, kind: str = "选股") -> None:
    """P2-21：校验 d 是否为 A 股交易日；不是则提示并要求 --force 才继续。

    用于 run-once / analyze 等"按交易日"语义的命令。回填类命令（backfill /
    refresh-cal）不调用此函数（因为本就需要跨非交易日的连续日期）。
    """
    from mo_stock.storage import repo

    with get_session() as session:
        if repo.is_trade_date(session, d):
            return
    msg = f"{d.isoformat()} 不是 A 股交易日（周末 / 节假日 / 交易日历缺失）"
    if force:
        click.echo(f"[警告] {msg}，--force 已指定，继续运行 {kind} 流程", err=True)
        return
    raise click.UsageError(f"{msg}；如确需运行，加 --force")


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
@click.option(
    "--with-ths", is_flag=True,
    help="同时刷新同花顺概念板块（ths_index + ths_member），额外耗时 3-4 分钟",
)
@click.option(
    "--with-hm-list", is_flag=True,
    help="同时刷新游资名录（hot_money_list），约 30 秒；LhbFilter 席位识别依赖此表",
)
def refresh_basics(with_ths: bool, with_hm_list: bool) -> None:
    """刷新「股票元数据」慢变量表。

    \b
    默认（约 30 秒）：
      - stock_basic   ~5500 行：全 A 股代码/名称/行业/上市日/ST 标记
      - index_member  ~5700 行：股票→申万一/二/三级行业映射（SectorFilter 关联 sw_daily 用）

    \b
    --with-ths（额外约 3-4 分钟）：
      - ths_index    ~408 行：同花顺 A 股概念板块元数据（新能源车/AI/华为产业链 等）
      - ths_member   ~7 万行：股票→概念多对多映射（ThemeFilter 题材命中加分用）

    \b
    --with-hm-list（额外约 30 秒）：
      - hot_money_list  ~109 行：Tushare 游资名录（赵老哥 / 章盟主 等）
        LhbFilter 用于把龙虎榜席位识别为 hot_money 类型

    \b
    推荐频率：
      - 默认：**每周 1 次**（周一开盘前）—— stock_basic 周度变化、index_member 申万年度评审
      - --with-ths：**每月 1 次** —— 同花顺概念变化频率介于两者之间
      - --with-hm-list：**每周 1 次** —— 游资名录低频更新
      - 不需要每天跑（浪费 Tushare 配额）

    详见 docs/cli.md 的「运行频率速查」章节。
    """
    ingestor = DailyIngestor()
    ingestor.refresh_stock_basic()
    ingestor.refresh_index_member()
    if with_ths:
        ingestor.refresh_ths_concept()
    if with_hm_list:
        ingestor.refresh_hot_money_list()


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
@click.option("--skip-enhanced", is_flag=True, help="只跑 6 个 CORE ingest，跳过题材/席位增强（5 步）")
@click.option("--force", is_flag=True, help="允许在非交易日运行（默认会拒绝）")
def run_once(date_str: str | None, skip_ingest: bool, skip_enhanced: bool, force: bool) -> None:
    """对指定交易日跑一次端到端选股流程：ingest → filter → combine → report。"""
    trade_date = _parse_date(date_str) if date_str else date.today()
    _ensure_trade_date(trade_date, force=force, kind="run-once")
    logger.info("=== run-once {} ===", trade_date)

    # ---------- 1. 数据拉取 ----------
    if not skip_ingest:
        DailyIngestor().ingest_one_day(trade_date, skip_enhanced=skip_enhanced)

    # ---------- 2. 加载权重配置 ----------
    weights_path = Path(__file__).resolve().parent.parent.parent / "config" / "weights.yaml"
    cfg = load_weights_yaml(weights_path)
    dim_weights: dict[str, float] = cfg.get("dimension_weights", {})
    hard_reject: dict = cfg.get("hard_reject", {})

    # ---------- 3. 规则层打分（4 维度：limit + moneyflow + lhb + sector）----------
    limit_filter = LimitFilter(weights=cfg.get("limit_filter", {}))
    mf_filter = MoneyflowFilter(weights=cfg.get("moneyflow_filter", {}))
    lhb_filter = LhbFilter(weights=cfg.get("lhb_filter", {}))
    sector_filter = SectorFilter(weights=cfg.get("sector_filter", {}))

    with get_session() as session:
        all_scores = [
            *limit_filter.score_all(session, trade_date),
            *mf_filter.score_all(session, trade_date),
            *lhb_filter.score_all(session, trade_date),
            *sector_filter.score_all(session, trade_date),
        ]

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
            # phase 默认值在 render_md.render_daily_report 里集中维护
        )

    click.echo(f"[OK] 报告已生成：\n  {md_path}\n  {json_path}")


@cli.command("analyze")
@click.argument("ts_code")
@click.option("--date", "date_str", default=None, help="分析的交易日 YYYY-MM-DD，默认今日")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出（便于脚本解析）")
@click.option("--force", is_flag=True, help="允许在非交易日运行（默认会拒绝）")
def analyze(ts_code: str, date_str: str | None, as_json: bool, force: bool) -> None:
    """对单只股票跑一次规则层分析（不写库）。

    示例：
        mo-stock analyze 600519.SH --date 2026-04-22
    """
    import json as _json

    trade_date = _parse_date(date_str) if date_str else date.today()
    _ensure_trade_date(trade_date, force=force, kind="analyze")

    with get_session() as session:
        result = analyze_stock(session, ts_code, trade_date)

    # JSON 模式：原样输出，date 对象用 default=str 序列化
    if as_json:
        click.echo(_json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    # 人类友好文本输出
    basic = result["basic"]
    name = basic["name"] if basic else "-"
    click.echo(f"=== {ts_code} {name}  @ {trade_date} ===")

    if basic:
        click.echo(
            f"行业: {basic.get('industry') or '-'}  |  申万一级: {basic.get('sw_l1') or '-'}"
            f"  |  上市日: {basic.get('list_date') or '-'}  |  ST: {basic.get('is_st')}"
        )
    else:
        click.echo("（stock_basic 表无此股记录，请先跑 refresh-basics）")

    # 当日行情
    kline = result["kline"]
    if kline:
        close = kline.get("close")
        pct = kline.get("pct_chg")
        close_str = f"{close:.2f}" if close is not None else "-"
        pct_str = f"{pct:+.2f}%" if pct is not None else "-"
        click.echo(f"K 线: 收盘 {close_str}  涨跌幅 {pct_str}")
    else:
        click.echo("K 线: 当日无数据")

    # 综合分 + 各维度
    click.echo(f"规则综合分 rule_score = {result['rule_score']:.2f}")
    if result["dimensions"]:
        for dim, info in result["dimensions"].items():
            click.echo(f"  [{dim:>10}] {info['score']:.2f}  detail={info['detail']}")
    else:
        click.echo("  （本股在当日所有维度均无得分记录）")

    # 硬规则
    hr = result["hard_reject"]
    if hr["rejected"]:
        click.echo(f"硬规则命中：{hr['reason']}")
    else:
        click.echo("硬规则：通过")


@cli.command("scheduler")
@click.option("--skip-enhanced", is_flag=True, help="scheduler 每日任务跳过 ENHANCED ingest（5 步）")
def scheduler(skip_enhanced: bool) -> None:
    """启动常驻调度：每个交易日 15:30 自动跑 run-once。"""
    from mo_stock.scheduler.daily_job import start_scheduler

    start_scheduler(skip_enhanced=skip_enhanced)


def main() -> None:
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
