"""short backtest trade table

Revision ID: 20260507_short_backtest_trade
Revises: f55e185ad212
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260507_short_backtest_trade"
down_revision = "f55e185ad212"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_backtest_trade",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column("backtest_run_id", sa.String(length=36), nullable=False, comment="回测批次 UUID"),
        sa.Column("signal_date", sa.Date(), nullable=False, comment="选股信号日 T"),
        sa.Column("entry_date", sa.Date(), nullable=True, comment="买入日 T+1"),
        sa.Column("ts_code", sa.String(length=12), nullable=False, comment="股票代码"),
        sa.Column("holding_days", sa.Integer(), nullable=False, comment="计划持有交易日数"),
        sa.Column("exit_date", sa.Date(), nullable=True, comment="实际退出日（含止损/停牌顺延）"),
        sa.Column("exit_price", sa.Float(), nullable=True, comment="实际退出价"),
        sa.Column("rule_score", sa.Numeric(precision=5, scale=2), nullable=True,
                  comment="信号日规则综合分"),
        sa.Column("active_dims", sa.Integer(), nullable=False, comment="命中维度数"),
        sa.Column("dim_detail", JSONB(astext_type=sa.Text()), nullable=True, comment="各维度得分细节"),
        sa.Column("rank_in_day", sa.Integer(), nullable=True, comment="当日排名（含 cap 后）"),
        sa.Column("sector_l1", sa.String(length=50), nullable=True, comment="申万一级行业（信号日快照）"),
        sa.Column("entry_price", sa.Float(), nullable=True, comment="买入价（T+1 open）"),
        sa.Column("raw_return_pct", sa.Float(), nullable=True, comment="自然持有收益 %（不止损，扣费前）"),
        sa.Column("realized_return_pct", sa.Float(), nullable=True, comment="真实收益 %（执行止损后，扣费前）"),
        sa.Column("net_raw_return_pct", sa.Float(), nullable=True, comment="自然持有收益 %（不止损，扣费后）"),
        sa.Column("net_realized_return_pct", sa.Float(), nullable=True,
                  comment="真实收益 %（执行止损后，扣费后）"),
        sa.Column("max_return_pct", sa.Float(), nullable=True, comment="持有期内最大收益 %"),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True, comment="持有期内最大回撤 %"),
        sa.Column("stop_hit", sa.Boolean(), nullable=False, comment="本持有期内是否触发止损"),
        sa.Column("stop_day", sa.Integer(), nullable=True, comment="止损触发在第几天"),
        sa.Column("exit_reason", sa.String(length=20), nullable=True,
                  comment="time_exit / stop_loss / incomplete / skipped"),
        sa.Column("detail", JSONB(astext_type=sa.Text()), nullable=True, comment="扩展信息"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "backtest_run_id", "signal_date", "ts_code", "holding_days",
            name="uq_short_bt_signal_hold",
        ),
        comment="短线回测交易记录（每信号 × 每持有期一行）",
    )
    op.create_index("ix_short_bt_run", "short_backtest_trade", ["backtest_run_id"])
    op.create_index("ix_short_bt_signal", "short_backtest_trade", ["signal_date"])
    op.create_index("ix_short_bt_code", "short_backtest_trade", ["ts_code"])
    op.create_index("ix_short_bt_hold", "short_backtest_trade", ["holding_days"])
    op.create_index("ix_short_bt_exit", "short_backtest_trade", ["exit_date"])


def downgrade() -> None:
    op.drop_index("ix_short_bt_exit", table_name="short_backtest_trade")
    op.drop_index("ix_short_bt_hold", table_name="short_backtest_trade")
    op.drop_index("ix_short_bt_code", table_name="short_backtest_trade")
    op.drop_index("ix_short_bt_signal", table_name="short_backtest_trade")
    op.drop_index("ix_short_bt_run", table_name="short_backtest_trade")
    op.drop_table("short_backtest_trade")

