"""theme and lhb seat tables (v2.1 plan)

Revision ID: 20260426_theme_lhb_v21
Revises:
Create Date: 2026-04-26

v2.1 plan Task 7：
- DROP `lhb.seat` JSONB 字段（v2.1 替换为独立表）
- CREATE 6 张新表（题材增强 + 龙虎榜席位明细 + 游资名录）

**前置条件**：
- 旧库已用 `mo-stock init-db` 建好基础 14 张表（含旧 lhb.seat 列）
- 或：旧库已 `alembic stamp` 到本 revision 之前的某个基线

**新部署**（无 alembic 历史）：直接用 `mo-stock init-db` 建全部表（已含本次新表）；
然后 `alembic stamp 20260426_theme_lhb_v21` 把版本对齐到当前。

**已有旧库**：先 `alembic stamp <prev>` 标基线（若没有则用 `alembic stamp base`），
再 `alembic upgrade head` 应用本 migration。

人工 review 已确认（v2.1 plan §Task 7 Step 2）：
- [x] 6 张新表都有 op.create_table
- [x] lhb.seat 已 op.drop_column
- [x] 索引（ix_ths_daily_date_pct / ix_lhb_seat_date_type 等）齐全
- [x] downgrade() 反向操作完整
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260426_theme_lhb_v21"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ====================================================================
    # 1. DROP Lhb.seat JSONB（v2.1：席位明细搬到独立表 lhb_seat_detail）
    # ====================================================================
    # 旧字段当前永远 None，无消费方，drop 不会丢失业务数据
    op.drop_column("lhb", "seat")

    # ====================================================================
    # 2. CREATE 题材增强 3 张表
    # ====================================================================

    # ths_daily：同花顺概念/行业指数日行情
    op.create_table(
        "ths_daily",
        sa.Column("ts_code", sa.String(length=20), nullable=False, comment="同花顺板块代码，如 885806.TI"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("name", sa.String(length=50), nullable=True, comment="板块名称（冗余 ths_index）"),
        sa.Column("close", sa.Float(), nullable=True, comment="收盘点位"),
        sa.Column("open", sa.Float(), nullable=True, comment="开盘点位"),
        sa.Column("high", sa.Float(), nullable=True, comment="最高"),
        sa.Column("low", sa.Float(), nullable=True, comment="最低"),
        sa.Column("pre_close", sa.Float(), nullable=True, comment="昨收"),
        sa.Column("avg_price", sa.Float(), nullable=True, comment="平均价"),
        sa.Column("change", sa.Float(), nullable=True, comment="涨跌额"),
        sa.Column("pct_change", sa.Float(), nullable=True, comment="涨跌幅（%）"),
        sa.Column("vol", sa.Float(), nullable=True, comment="成交量"),
        sa.Column("turnover_rate", sa.Float(), nullable=True, comment="换手率（%）"),
        sa.Column("total_mv", sa.Float(), nullable=True, comment="总市值"),
        sa.Column("float_mv", sa.Float(), nullable=True, comment="流通市值"),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
        comment="同花顺概念/行业指数日行情，ThemeFilter 强度输入",
    )
    op.create_index("ix_ths_daily_date_pct", "ths_daily", ["trade_date", "pct_change"])

    # limit_concept_daily：涨停最强概念
    op.create_table(
        "limit_concept_daily",
        sa.Column("ts_code", sa.String(length=20), nullable=False, comment="板块代码"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("name", sa.String(length=50), nullable=True, comment="板块名称"),
        sa.Column("days", sa.Integer(), nullable=True, comment="上榜天数"),
        sa.Column("up_stat", sa.String(length=50), nullable=True, comment="连板高度描述"),
        sa.Column("cons_nums", sa.Integer(), nullable=True, comment="连板家数"),
        sa.Column("up_nums", sa.Integer(), nullable=True, comment="涨停家数"),
        sa.Column("pct_chg", sa.Float(), nullable=True, comment="概念涨跌幅（%）"),
        sa.Column("rank", sa.Integer(), nullable=True, comment="热点排名，1 最强"),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
        comment="每日涨停最强概念板块，短线题材热度输入",
    )
    op.create_index(
        "ix_limit_concept_date_rank", "limit_concept_daily", ["trade_date", "rank"],
    )

    # ths_concept_moneyflow：概念资金流（3 净额字段全保留）
    op.create_table(
        "ths_concept_moneyflow",
        sa.Column("ts_code", sa.String(length=20), nullable=False, comment="概念板块代码"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("name", sa.String(length=50), nullable=True, comment="板块名称"),
        sa.Column("lead_stock", sa.String(length=50), nullable=True, comment="领涨股票名称"),
        sa.Column("pct_change", sa.Float(), nullable=True, comment="板块涨跌幅（%）"),
        sa.Column("company_num", sa.Integer(), nullable=True, comment="成分公司数量"),
        sa.Column("pct_change_stock", sa.Float(), nullable=True, comment="领涨股涨跌幅（%）"),
        sa.Column("net_buy_amount", sa.Float(), nullable=True, comment="买入额（亿元）"),
        sa.Column("net_sell_amount", sa.Float(), nullable=True, comment="卖出额（亿元）"),
        sa.Column("net_amount", sa.Float(), nullable=True, comment="净流入额（亿元）"),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
        comment="概念板块资金流向，ThemeFilter 资金确认输入",
    )
    op.create_index(
        "ix_concept_moneyflow_date_net", "ths_concept_moneyflow",
        ["trade_date", "net_amount"],
    )

    # ====================================================================
    # 3. CREATE 龙虎榜席位 + 游资名录 3 张表
    # ====================================================================

    # hot_money_list：游资名录
    op.create_table(
        "hot_money_list",
        sa.Column("name", sa.String(length=100), nullable=False, comment="游资名称（Tushare 主键）"),
        sa.Column("desc", sa.Text(), nullable=True, comment="游资风格说明"),
        sa.Column("orgs", sa.Text(), nullable=True, comment="关联营业部，分号/逗号分隔"),
        sa.PrimaryKeyConstraint("name"),
        comment="Tushare 游资名录，用于龙虎榜席位身份识别",
    )

    # hot_money_detail：游资每日交易明细
    op.create_table(
        "hot_money_detail",
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("ts_code", sa.String(length=12), nullable=False, comment="股票代码"),
        sa.Column("hm_name", sa.String(length=100), nullable=False, comment="游资名称"),
        sa.Column("ts_name", sa.String(length=50), nullable=True, comment="股票名称"),
        sa.Column("buy_amount", sa.Float(), nullable=True, comment="买入金额（元）"),
        sa.Column("sell_amount", sa.Float(), nullable=True, comment="卖出金额（元）"),
        sa.Column("net_amount", sa.Float(), nullable=True, comment="净买卖金额（元）"),
        sa.Column("hm_orgs", sa.Text(), nullable=True, comment="关联营业部"),
        sa.Column("tag", sa.String(length=50), nullable=True, comment="标签"),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "hm_name"),
        comment="每日游资交易明细，LhbFilter 席位身份加权辅助",
    )
    op.create_index("ix_hot_money_detail_date", "hot_money_detail", ["trade_date"])
    op.create_index(
        "ix_hot_money_detail_hm", "hot_money_detail", ["hm_name", "trade_date"],
    )

    # lhb_seat_detail：龙虎榜席位明细（PK 用 seat_key 内容寻址）
    op.create_table(
        "lhb_seat_detail",
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("ts_code", sa.String(length=12), nullable=False, comment="股票代码"),
        sa.Column(
            "seat_key", sa.String(length=64), nullable=False,
            comment="稳定席位键 = sha1(ts_code|exalter|side|reason)",
        ),
        sa.Column(
            "seat_no", sa.Integer(), nullable=False,
            comment="展示序号 1-N（稳定排序后），消费方排序请用 seat_no",
        ),
        sa.Column("exalter", sa.String(length=200), nullable=True, comment="席位/营业部名称"),
        sa.Column("side", sa.String(length=2), nullable=True, comment="0=买榜 / 1=卖榜"),
        sa.Column("buy", sa.Float(), nullable=True, comment="买入金额（元）"),
        sa.Column("sell", sa.Float(), nullable=True, comment="卖出金额（元）"),
        sa.Column("net_buy", sa.Float(), nullable=True, comment="净买卖金额（元）"),
        sa.Column("reason", sa.String(length=100), nullable=True, comment="上榜原因"),
        sa.Column(
            "seat_type", sa.String(length=20), nullable=False,
            comment="institution / northbound / hot_money / other",
        ),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "seat_key"),
        comment="龙虎榜席位明细（v2.1 替代 Lhb.seat JSONB），LhbFilter 输入",
    )
    op.create_index("ix_lhb_seat_date_type", "lhb_seat_detail", ["trade_date", "seat_type"])
    op.create_index("ix_lhb_seat_date_code", "lhb_seat_detail", ["trade_date", "ts_code"])


def downgrade() -> None:
    # 反向：先 DROP 6 张表，再恢复 lhb.seat 列（数据不会恢复，仅结构）

    op.drop_index("ix_lhb_seat_date_code", table_name="lhb_seat_detail")
    op.drop_index("ix_lhb_seat_date_type", table_name="lhb_seat_detail")
    op.drop_table("lhb_seat_detail")

    op.drop_index("ix_hot_money_detail_hm", table_name="hot_money_detail")
    op.drop_index("ix_hot_money_detail_date", table_name="hot_money_detail")
    op.drop_table("hot_money_detail")

    op.drop_table("hot_money_list")

    op.drop_index("ix_concept_moneyflow_date_net", table_name="ths_concept_moneyflow")
    op.drop_table("ths_concept_moneyflow")

    op.drop_index("ix_limit_concept_date_rank", table_name="limit_concept_daily")
    op.drop_table("limit_concept_daily")

    op.drop_index("ix_ths_daily_date_pct", table_name="ths_daily")
    op.drop_table("ths_daily")

    # 恢复 lhb.seat JSONB（无历史数据回填）
    op.add_column(
        "lhb",
        sa.Column("seat", JSONB(astext_type=sa.Text()), nullable=True,
                  comment="席位明细 JSON（历史字段，v2.1 已废弃）"),
    )
