"""strategy isolation and swing position phase 0

Revision ID: 20260430_strategy_swing_phase0
Revises: 20260426_theme_lhb_v21
Create Date: 2026-04-30

Phase 0:
- Add strategy column to selection_result / filter_score_daily / ai_analysis.
- Backfill existing rows to strategy='short'.
- Replace unique constraints with strategy-aware keys.
- Create swing_position for future swing backtest/live tracking.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260430_strategy_swing_phase0"
down_revision = "20260426_theme_lhb_v21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add strategy, backfill old rows as short, then enforce NOT NULL.
    for table in ("selection_result", "filter_score_daily", "ai_analysis"):
        op.add_column(
            table,
            sa.Column(
                "strategy",
                sa.String(length=20),
                nullable=True,
                server_default="short",
                comment="策略标识：short / swing",
            ),
        )
        op.execute(sa.text(f"UPDATE {table} SET strategy = 'short' WHERE strategy IS NULL"))
        op.alter_column(table, "strategy", nullable=False, server_default="short")
        op.create_index(f"ix_{table}_strategy", table, ["strategy"])

    # 2. Replace unique constraints.
    op.drop_constraint("uq_selection_key", "selection_result", type_="unique")
    op.create_unique_constraint(
        "uq_selection_key",
        "selection_result",
        ["trade_date", "strategy", "ts_code"],
    )

    op.drop_constraint("uq_filter_score_key", "filter_score_daily", type_="unique")
    op.create_unique_constraint(
        "uq_filter_score_key",
        "filter_score_daily",
        ["trade_date", "strategy", "ts_code", "dim"],
    )

    op.drop_constraint("uq_ai_analysis_key", "ai_analysis", type_="unique")
    op.create_unique_constraint(
        "uq_ai_analysis_key",
        "ai_analysis",
        ["trade_date", "strategy", "ts_code"],
    )

    # 3. Replace strategy-aware read indexes.
    op.drop_index("ix_filter_score_date_dim", table_name="filter_score_daily")
    op.create_index(
        "ix_filter_score_date_strategy_dim",
        "filter_score_daily",
        ["trade_date", "strategy", "dim"],
    )

    op.drop_index("ix_selection_date_rank", table_name="selection_result")
    op.create_index(
        "ix_selection_date_strategy_rank",
        "selection_result",
        ["trade_date", "strategy", "rank"],
    )
    op.create_index(
        "ix_ai_analysis_date_strategy",
        "ai_analysis",
        ["trade_date", "strategy"],
    )

    # 4. Swing position state table.
    op.create_table(
        "swing_position",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column("mode", sa.String(length=20), nullable=False, comment="运行模式：backtest / live"),
        sa.Column("backtest_run_id", sa.String(length=36), nullable=True,
                  comment="回测批次 ID（mode=backtest 时填写，如 UUID）；live 时为 NULL"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="记录日期"),
        sa.Column("ts_code", sa.String(length=12), nullable=False, comment="股票代码"),
        sa.Column("status", sa.String(length=20), nullable=False,
                  comment="持仓状态：watching / holding / stopped / exited"),
        sa.Column("entry_price", sa.Float(), nullable=True, comment="入场价"),
        sa.Column("entry_date", sa.Date(), nullable=True, comment="入场日期"),
        sa.Column("stop_loss_price", sa.Float(), nullable=True,
                  comment="当前止损价；watching 状态可为空"),
        sa.Column("target_price", sa.Float(), nullable=True, comment="目标价"),
        sa.Column("atr_at_entry", sa.Float(), nullable=True, comment="入场时 ATR(20)"),
        sa.Column("max_price", sa.Float(), nullable=True, comment="持仓期最高价（移动止盈用）"),
        sa.Column("pnl_pct", sa.Float(), nullable=True, comment="当前浮动盈亏 %"),
        sa.Column("exit_reason", sa.String(length=50), nullable=True, comment="退出原因"),
        sa.Column("holding_days", sa.Integer(), nullable=True, comment="持仓交易日数"),
        sa.Column("detail", JSONB(astext_type=sa.Text()), nullable=True, comment="补充信息"),
        sa.PrimaryKeyConstraint("id"),
        comment="波段持仓跟踪（回测/实盘通过 mode 隔离，回测用 backtest_run_id 批次管理）",
    )
    op.create_index("ix_swing_position_mode", "swing_position", ["mode"])
    op.create_index("ix_swing_position_backtest_run_id", "swing_position", ["backtest_run_id"])
    op.create_index("ix_swing_position_trade_date", "swing_position", ["trade_date"])
    op.create_index("ix_swing_position_ts_code", "swing_position", ["ts_code"])
    op.create_index("ix_swing_pos_mode_date", "swing_position", ["mode", "trade_date"])
    op.create_index("ix_swing_pos_run_id", "swing_position", ["backtest_run_id"])


def downgrade() -> None:
    op.drop_index("ix_swing_pos_run_id", table_name="swing_position")
    op.drop_index("ix_swing_pos_mode_date", table_name="swing_position")
    op.drop_index("ix_swing_position_ts_code", table_name="swing_position")
    op.drop_index("ix_swing_position_trade_date", table_name="swing_position")
    op.drop_index("ix_swing_position_backtest_run_id", table_name="swing_position")
    op.drop_index("ix_swing_position_mode", table_name="swing_position")
    op.drop_table("swing_position")

    op.drop_index("ix_ai_analysis_date_strategy", table_name="ai_analysis")
    op.drop_index("ix_selection_date_strategy_rank", table_name="selection_result")
    op.create_index("ix_selection_date_rank", "selection_result", ["trade_date", "rank"])

    op.drop_index("ix_filter_score_date_strategy_dim", table_name="filter_score_daily")
    op.create_index("ix_filter_score_date_dim", "filter_score_daily", ["trade_date", "dim"])

    op.drop_constraint("uq_ai_analysis_key", "ai_analysis", type_="unique")
    op.create_unique_constraint("uq_ai_analysis_key", "ai_analysis", ["trade_date", "ts_code"])

    op.drop_constraint("uq_filter_score_key", "filter_score_daily", type_="unique")
    op.create_unique_constraint(
        "uq_filter_score_key",
        "filter_score_daily",
        ["trade_date", "ts_code", "dim"],
    )

    op.drop_constraint("uq_selection_key", "selection_result", type_="unique")
    op.create_unique_constraint("uq_selection_key", "selection_result", ["trade_date", "ts_code"])

    for table in ("ai_analysis", "filter_score_daily", "selection_result"):
        op.drop_index(f"ix_{table}_strategy", table_name=table)
        op.drop_column(table, "strategy")
