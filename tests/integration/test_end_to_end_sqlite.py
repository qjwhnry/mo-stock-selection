"""端到端模拟测试：用 SQLite + JSONB→JSON variant 跑通整个 pipeline。

目的：在不依赖 PostgreSQL 的前提下验证 filters / combine / report 的逻辑链路是否闭合。
**注意**：
- 此测试只能覆盖"读 + 规则打分 + 生成报告"链路
- 生产 upsert 依赖 PG 的 `ON CONFLICT`，此测试不覆盖（跳过 persist_filter_scores）
- 直接用 ORM 对象 session.add → commit 写入，绕过 pg_insert
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import JSON


def _register_jsonb_on_sqlite() -> None:
    """给 SQLite dialect 打补丁，使 JSONB 编译为 JSON。"""
    from sqlalchemy.dialects.sqlite import base as sqlite_base

    if not hasattr(sqlite_base.SQLiteTypeCompiler, "visit_JSONB"):
        def visit_JSONB(self, type_, **kw):  # noqa: N802
            return self.process(JSON(), **kw)

        sqlite_base.SQLiteTypeCompiler.visit_JSONB = visit_JSONB


@pytest.fixture
def populated_session(tmp_path):
    """每个测试都起一个全新的 SQLite 文件库，注入固定测试数据。"""
    _register_jsonb_on_sqlite()

    from mo_stock.storage.models import (
        Base,
        DailyKline,
        LimitList,
        Moneyflow,
        StockBasic,
        TradeCal,
    )

    db_file = tmp_path / "test.sqlite"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
    session = Session()

    trade_date = date(2026, 4, 22)

    # 交易日历
    session.add(TradeCal(cal_date=trade_date, is_open=True))

    # 股票基础表
    stocks = [
        ("600519.SH", "贵州茅台"),
        ("000001.SZ", "平安银行"),
        ("300750.SZ", "宁德时代"),
        ("601318.SH", "中国平安"),
        ("002594.SZ", "比亚迪"),
    ]
    for ts_code, name in stocks:
        session.add(StockBasic(
            ts_code=ts_code,
            symbol=ts_code.split(".")[0],
            name=name,
            is_st=False,
            list_date=date(2000, 1, 1),
        ))

    # 涨停：300750 首板强势封板；000001 炸板 2 次（应被过滤为 0 分）
    session.add(LimitList(
        ts_code="300750.SZ",
        trade_date=trade_date,
        limit_type="U",
        fd_amount=50000 * 10000,   # 5 亿
        first_time="09:35:00",     # 10:00 前：+15
        last_time="14:55:00",
        open_times=0,
        up_stat="1/1",
        limit_times=1,
    ))
    session.add(LimitList(
        ts_code="000001.SZ",
        trade_date=trade_date,
        limit_type="U",
        fd_amount=1000 * 10000,    # 0.1 亿
        first_time="11:05:00",     # 11:00 后：+5
        last_time="14:40:00",
        open_times=3,              # 炸板 3 次 → hard_fail=0
        up_stat="2/3",
        limit_times=2,
    ))

    # 资金流：600519 主力大单净入；601318 弱流入；002594 当日净流出
    session.add(Moneyflow(
        ts_code="600519.SH",
        trade_date=trade_date,
        net_mf_amount=8000,        # 万元，主力净流入 8000 万
        buy_sm_amount=1000, sell_sm_amount=1200,
        buy_md_amount=3000, sell_md_amount=2000,
        buy_lg_amount=10000, sell_lg_amount=3000,
        buy_elg_amount=5000, sell_elg_amount=800,
    ))
    session.add(Moneyflow(
        ts_code="601318.SH",
        trade_date=trade_date,
        net_mf_amount=500,         # 弱净入
        buy_sm_amount=3000, sell_sm_amount=1000,
        buy_md_amount=800, sell_md_amount=500,
        buy_lg_amount=600, sell_lg_amount=700,
        buy_elg_amount=100, sell_elg_amount=300,
    ))
    session.add(Moneyflow(
        ts_code="002594.SZ",
        trade_date=trade_date,
        net_mf_amount=-5000,       # 净流出（应 score=0 退出）
        buy_sm_amount=2000, sell_sm_amount=3000,
        buy_md_amount=1000, sell_md_amount=2000,
        buy_lg_amount=500, sell_lg_amount=1500,
        buy_elg_amount=0, sell_elg_amount=500,
    ))

    # 日线
    # amount 单位：千元（与 Tushare daily 接口一致）。MoneyflowFilter 用它作为 net_mf_amount
    # 占比的分母（ratio_pct = 1000 × net_mf_wan / amount_qy），fixture 必须给非空合理值，
    # 否则 _today_bonus_tier 返回 0 → 整只股 continue 跳过，results 为空、断言全挂。
    for ts_code, close, amount in [
        ("600519.SH", 1780.5, 985000.0),   # 9.85 亿元；配 net_mf=8000 万 → ratio≈8.12% → today_bonus=50
        ("000001.SZ", 11.35, 200000.0),    # 仅供 LimitFilter，moneyflow 不到这只
        ("300750.SZ", 285.0, 600000.0),    # 同上
        ("601318.SH", 48.2, 300000.0),     # 3 亿元；配 net_mf=500 万 → ratio≈1.67% → today_bonus=35
        ("002594.SZ", 220.0, 350000.0),    # net_mf<0 已被跳过，amount 任意
    ]:
        session.add(DailyKline(
            ts_code=ts_code,
            trade_date=trade_date,
            close=close,
            pct_chg=5.0 if ts_code in ("300750.SZ", "000001.SZ") else 1.2,
            amount=amount,
        ))

    session.commit()
    yield session
    session.close()
    engine.dispose()


class TestFiltersEndToEnd:
    def test_limit_filter_scores(self, populated_session):
        """验证 LimitFilter 对当日两只涨停股的打分逻辑。"""
        from mo_stock.filters.short.limit_filter import LimitFilter

        f = LimitFilter(weights={
            "first_board_bonus": 20,
            "second_board_bonus": 30,
            "open_times_penalty": 10,
            "seal_amount_tier": [
                {"threshold": 1.0, "score": 10},
                {"threshold": 5.0, "score": 20},
            ],
        })
        results = f.score_all(populated_session, date(2026, 4, 22))

        # 两只涨停股都要有记录
        by_code = {r.ts_code: r for r in results}
        assert set(by_code.keys()) == {"300750.SZ", "000001.SZ"}

        # 300750：首板(20) + 封单 5 亿(20) + 09:35 封板(15) = 55
        assert by_code["300750.SZ"].score == 55.0

        # 000001：炸板 ≥2 次硬淘汰为 0，detail 里有 hard_fail
        assert by_code["000001.SZ"].score == 0.0
        assert by_code["000001.SZ"].detail.get("hard_fail") is not None

    def test_moneyflow_filter_scores(self, populated_session):
        """验证 MoneyflowFilter 的打分与负信号检测。"""
        from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter

        f = MoneyflowFilter(weights={
            "today_net_inflow_bonus": 20,
            "rolling_3d_bonus": 15,
            "big_order_ratio_threshold": 0.4,
            "small_up_big_down_penalty": 30,
        })
        results = f.score_all(populated_session, date(2026, 4, 22))

        by_code = {r.ts_code: r for r in results}

        # 仅净流入的股票会被打分；净流出的 002594 直接跳过（不 append，避免污染综合分）
        assert set(by_code.keys()) == {"600519.SH", "601318.SH"}

        # 600519：当日净入占比 8.12% ≥5% → today_bonus=50（封顶）
        # + 大单占比 0.596 > 0.4 → ratio_bonus=30 + 3 日正 → +15。score = 95
        assert by_code["600519.SH"].score >= 50.0

        # 601318：当日净入占比 1.67% → 连续 today_bonus ≈ 16.6
        # 小单净入 + 大单净出 → -30 惩罚；3 日正 +15。最终 ≈ 1.6
        assert by_code["601318.SH"].score == pytest.approx(1.63, abs=0.5)
