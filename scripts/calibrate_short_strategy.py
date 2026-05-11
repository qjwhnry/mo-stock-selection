#!/usr/bin/env python
"""短线策略校准脚本：读最新回测批次 → 输出校准报告 markdown。

用法：
    .venv/bin/python scripts/calibrate_short_strategy.py [--run-id UUID] [--holding-days 1]

不传 --run-id 时取 short_backtest_trade 表里最新的 backtest_run_id。
输出文件名格式：docs/short-strategy-calibration-YYYY-MM-DD-{holding_days}d.md
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import desc, func, select

from mo_stock.backtest.calibration import (
    catalyst_dims_groups,
    dim_combination_analysis,
    exhaustion_quality_buckets,
    rank_in_day_groups,
    render_markdown_report,
    rule_score_buckets,
    sector_groups,
)
from mo_stock.storage.db import get_session
from mo_stock.storage.models import ShortBacktestTrade


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="回测批次 UUID；不传取最新")
    parser.add_argument("--holding-days", type=int, default=1, help="持有期（默认 1d）")
    parser.add_argument("--output-dir", default="docs", help="报告输出目录（默认 docs/）")
    parser.add_argument(
        "--return-field",
        default="net_realized_return_pct",
        help="收益字段（默认 net_realized_return_pct，扣费后）",
    )
    args = parser.parse_args()

    with get_session() as sess:
        run_id = args.run_id or _latest_run_id(sess)
        if not run_id:
            print("未找到任何回测批次，请先跑 mo-stock backtest --strategy short")
            return

        trades = sess.execute(
            select(ShortBacktestTrade).where(
                ShortBacktestTrade.backtest_run_id == run_id
            )
        ).scalars().all()

        if not trades:
            print(f"回测批次 {run_id} 无任何交易记录")
            return

        window = (
            min(t.signal_date for t in trades),
            max(t.signal_date for t in trades),
        )

        rule_buckets = rule_score_buckets(
            trades, holding_days=args.holding_days, return_field=args.return_field
        )
        catalyst = catalyst_dims_groups(
            trades, holding_days=args.holding_days, return_field=args.return_field
        )
        ranks = rank_in_day_groups(
            trades, holding_days=args.holding_days, return_field=args.return_field
        )
        sectors = sector_groups(
            trades,
            holding_days=args.holding_days,
            min_count=20,
            return_field=args.return_field,
        )
        combos = dim_combination_analysis(
            trades,
            holding_days=args.holding_days,
            min_count=5,
            return_field=args.return_field,
        )
        ex_buckets = exhaustion_quality_buckets(
            trades, holding_days=args.holding_days, return_field=args.return_field
        )

        hd_count = sum(1 for t in trades if t.holding_days == args.holding_days)

        md = render_markdown_report(
            run_id=run_id,
            backtest_window=window,
            holding_days=args.holding_days,
            return_field=args.return_field,
            total_trades=hd_count,
            rule_buckets=rule_buckets,
            catalyst_groups=catalyst,
            rank_groups=ranks,
            sector_buckets=sectors,
            dim_combos=combos,
            exhaustion_buckets=ex_buckets,
        )

    today = date.today().isoformat()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"short-strategy-calibration-{today}-{args.holding_days}d.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"校准报告已写入 {out_path}")


def _latest_run_id(sess) -> str | None:
    """取最新写入的批次。

    按 max(ShortBacktestTrade.id) 排序——同一回测区间多次重跑时，signal_date 一样
    会随机取到任意 run；id 是写入顺序，能稳定指向最近一次回测。
    """
    row = sess.execute(
        select(
            ShortBacktestTrade.backtest_run_id,
            func.max(ShortBacktestTrade.id),
        )
        .group_by(ShortBacktestTrade.backtest_run_id)
        .order_by(desc(func.max(ShortBacktestTrade.id)))
        .limit(1)
    ).first()
    return row[0] if row else None


if __name__ == "__main__":
    main()
