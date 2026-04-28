"""combine_scores 板块多样化 cap 集成测试（v2.3）。

验证 audit-sector-concentration-2026-04-28 P0 修复：
- 同一申万一级板块在 Top N 中最多 max_stocks_per_sector 只
- 仅入选股消耗板块名额（被硬规则淘汰 / Top N 外不消耗）
- sector=None 的股票（无板块归属）正常入选，不挤占其它板块名额
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import JSON


def _register_jsonb_on_sqlite() -> None:
    from sqlalchemy.dialects.sqlite import base as sqlite_base

    if not hasattr(sqlite_base.SQLiteTypeCompiler, "visit_JSONB"):
        def visit_JSONB(self, type_, **kw):  # noqa: N802
            return self.process(JSON(), **kw)

        sqlite_base.SQLiteTypeCompiler.visit_JSONB = visit_JSONB


@pytest.fixture
def session_with_scores():
    """构造 25 只股的 filter_score_daily：

    - 20 只属于板块 801080（电子元器件），高分（rule_score 50）
    - 4 只属于板块 801090（机械），中分（rule_score 30）
    - 1 只无板块归属（sector_map 中找不到），高分 50
    """
    _register_jsonb_on_sqlite()

    from mo_stock.storage.models import (
        Base,
        FilterScoreDaily,
        IndexMember,
        StockBasic,
    )

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
    session = Session()

    trade_date = date(2026, 4, 27)

    # 20 只高分元器件股 + 4 只中分机械股 + 1 只无板块股
    high_codes = [f"H{i:03d}.SH" for i in range(20)]
    mid_codes = [f"M{i:03d}.SH" for i in range(4)]
    orphan_code = "ORPH001.SH"

    for code in [*high_codes, *mid_codes, orphan_code]:
        session.add(StockBasic(
            ts_code=code,
            symbol=code.split(".")[0],
            name=code,
            is_st=False,
            list_date=date(2000, 1, 1),
        ))

    # 板块映射：高分股 → 801080，中分股 → 801090，孤儿股不写入 IndexMember
    for code in high_codes:
        session.add(IndexMember(ts_code=code, l1_code="801080.SI"))
    for code in mid_codes:
        session.add(IndexMember(ts_code=code, l1_code="801090.SI"))

    # 写入 filter_score_daily：每股一个 limit 维度的得分
    for code in high_codes:
        session.add(FilterScoreDaily(
            trade_date=trade_date, ts_code=code, dim="limit",
            score=50.0, detail={},
        ))
    for code in mid_codes:
        session.add(FilterScoreDaily(
            trade_date=trade_date, ts_code=code, dim="limit",
            score=30.0, detail={},
        ))
    session.add(FilterScoreDaily(
        trade_date=trade_date, ts_code=orphan_code, dim="limit",
        score=50.0, detail={},
    ))

    session.commit()
    yield session, trade_date, high_codes, mid_codes, orphan_code
    session.close()
    engine.dispose()


# 简化权重：只有 limit 维度，分母 1.0
_DIM_WEIGHTS = {"limit": 1.0}


class TestSectorCap:
    def test_cap_limits_dominant_sector(self, session_with_scores):
        """单板块 20 只高分 → Top 20 中 801080 不超过 4 只。"""
        from mo_stock.scorer.combine import combine_scores
        from mo_stock.storage.models import SelectionResult

        session, trade_date, _, _, _ = session_with_scores

        n = combine_scores(
            session, trade_date,
            dimension_weights=_DIM_WEIGHTS,
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={"max_stocks_per_sector": 4},
        )

        picked_rows = session.query(SelectionResult).filter_by(
            trade_date=trade_date, picked=True,
        ).all()
        from mo_stock.storage import repo
        sector_map = repo.get_index_member_l1_map(session)
        sectors = [sector_map.get(r.ts_code) for r in picked_rows]

        from collections import Counter
        counts = Counter(sectors)
        # 板块 801080 不超过 4
        assert counts.get("801080.SI", 0) <= 4
        # 板块 801090 不超过 4（虽然只有 4 只，正好入选）
        assert counts.get("801090.SI", 0) <= 4
        # 入选总数 = 4 (801080) + 4 (801090) + 1 (孤儿) = 9，少于 top_n=20
        # 因为板块 cap 卡住了 16 只元器件股
        assert n == 9

    def test_cap_disabled_when_zero(self, session_with_scores):
        """max_stocks_per_sector=0 → cap 禁用，行为等同 v2.2。"""
        from mo_stock.scorer.combine import combine_scores
        from mo_stock.storage.models import SelectionResult

        session, trade_date, _, _, _ = session_with_scores

        n = combine_scores(
            session, trade_date,
            dimension_weights=_DIM_WEIGHTS,
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={"max_stocks_per_sector": 0},
        )
        # 禁用 cap：取满 20 只（全是高分元器件 + 孤儿股）
        assert n == 20
        picked = session.query(SelectionResult).filter_by(
            trade_date=trade_date, picked=True,
        ).all()
        assert len(picked) == 20

    def test_orphan_stock_does_not_consume_quota(self, session_with_scores):
        """无板块归属（sector=None）的股票不消耗任何板块名额。"""
        from mo_stock.scorer.combine import combine_scores
        from mo_stock.storage.models import SelectionResult

        session, trade_date, _, _, orphan_code = session_with_scores

        combine_scores(
            session, trade_date,
            dimension_weights=_DIM_WEIGHTS,
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={"max_stocks_per_sector": 4},
        )

        orphan_row = session.query(SelectionResult).filter_by(
            trade_date=trade_date, ts_code=orphan_code,
        ).one()
        # 孤儿股得分 50，与 801080 高分股并列；它不属于任何板块 → 必入选
        assert orphan_row.picked is True


class TestSectorCapWithRejection:
    """混合硬规则淘汰场景：被淘汰的股不应消耗板块名额。"""

    def test_rejected_high_score_does_not_consume_quota(self, session_with_scores):
        """把 20 只高分元器件股中 16 只标记为 ST → 被淘汰，剩 4 只入选；
        cap=4 时这 4 只刚好不超限，且后续中分股仍能入选。"""
        from mo_stock.scorer.combine import combine_scores
        from mo_stock.storage.models import SelectionResult, StockBasic

        session, trade_date, high_codes, _, _ = session_with_scores

        # 把前 16 只高分股标 ST
        for code in high_codes[:16]:
            stock = session.query(StockBasic).filter_by(ts_code=code).one()
            stock.is_st = True
        session.commit()

        n = combine_scores(
            session, trade_date,
            dimension_weights=_DIM_WEIGHTS,
            hard_reject_cfg={
                "exclude_st": True, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={"max_stocks_per_sector": 4},
        )

        picked = session.query(SelectionResult).filter_by(
            trade_date=trade_date, picked=True,
        ).all()
        from mo_stock.storage import repo
        sector_map = repo.get_index_member_l1_map(session)
        from collections import Counter
        counts = Counter(sector_map.get(r.ts_code) for r in picked)

        # 关键断言：ST 被淘汰的 16 只不消耗 801080 板块名额
        # → 剩下 4 只 801080 高分股 + 4 只 801090 + 1 只孤儿 = 9 只入选
        assert counts.get("801080.SI", 0) == 4
        assert counts.get("801090.SI", 0) == 4
        assert n == 9


class TestEmptySectorMap:
    """index_member 同步异常 → 全市场 sector=None 时 cap 不应静默失效。"""

    def test_warns_and_falls_back_to_unknown_cap(
        self, session_with_scores, loguru_caplog,
    ):
        """sector_map 为空 + max_unknown=3 → 仅 3 只 unknown 股入选 + warning 必发。"""
        from mo_stock.scorer.combine import combine_scores
        from mo_stock.storage.models import IndexMember, SelectionResult

        session, trade_date, _, _, _ = session_with_scores
        # 清掉 IndexMember → 模拟刷新失败
        session.query(IndexMember).delete()
        session.commit()

        n = combine_scores(
            session, trade_date,
            dimension_weights=_DIM_WEIGHTS,
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={
                "max_stocks_per_sector": 4,
                "max_unknown_sector_stocks": 3,
            },
        )

        assert n == 3
        picked = session.query(SelectionResult).filter_by(
            trade_date=trade_date, picked=True,
        ).all()
        assert len(picked) == 3
        # 关键断言：sector_map 为空时必须发 warning（运维信号）
        assert any(
            "板块映射为空" in r.message for r in loguru_caplog.records
        ), f"未捕获到 sector_map 为空的 warning：{[r.message for r in loguru_caplog.records]}"


class TestReplaceFilterScores:
    """v2.3：replace_filter_scores 必须清掉旧维度脏分数（如旧版 sector_heat_bonus）。"""

    def test_replace_deletes_stale_dim_rows(self):
        """旧版 LimitFilter 写入的 limit=60（带 sector_heat_bonus）→
        新版 replace 后旧行必须消失。"""
        _register_jsonb_on_sqlite()

        from mo_stock.filters.base import ScoreResult
        from mo_stock.scorer.combine import replace_filter_scores
        from mo_stock.storage.models import Base, FilterScoreDaily

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
        session = Session()

        trade_date = date(2026, 4, 27)

        # 模拟旧版本残留：limit 维度 5 只股
        for code in ["A.SH", "B.SH", "C.SH", "D.SH", "E.SH"]:
            session.add(FilterScoreDaily(
                trade_date=trade_date, ts_code=code, dim="limit",
                score=60.0,
                detail={"sector_heat_bonus": 60, "sector_limit_count": 12},
            ))
        session.commit()
        assert session.query(FilterScoreDaily).count() == 5

        # 新版本本轮只产出 1 只股（A.SH）的 limit 分数（断板反包）
        new_results = [
            ScoreResult(
                ts_code="A.SH", trade_date=trade_date, dim="limit",
                score=50.0, detail={"break_board_rebound": 50},
            ),
        ]
        replace_filter_scores(
            session, trade_date,
            dims=["limit"], results=new_results,
        )
        session.commit()

        # 关键断言：旧 4 只脏数据消失，仅剩新版的 1 只
        rows = session.query(FilterScoreDaily).filter_by(
            trade_date=trade_date, dim="limit",
        ).all()
        assert len(rows) == 1
        assert rows[0].ts_code == "A.SH"
        assert rows[0].score == 50.0
        assert "sector_heat_bonus" not in (rows[0].detail or {})

        session.close()
        engine.dispose()

    def test_replace_preserves_other_dates_and_dims(self):
        """replace 只清当前 (trade_date, dim) 范围，不影响其它日期/维度。"""
        _register_jsonb_on_sqlite()

        from mo_stock.scorer.combine import replace_filter_scores
        from mo_stock.storage.models import Base, FilterScoreDaily

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
        session = Session()

        td_today = date(2026, 4, 27)
        td_yesterday = date(2026, 4, 26)

        # 隔离数据：昨日的 limit、今日的 sector，都不应被清
        session.add(FilterScoreDaily(
            trade_date=td_yesterday, ts_code="A.SH", dim="limit",
            score=42.0, detail={},
        ))
        session.add(FilterScoreDaily(
            trade_date=td_today, ts_code="A.SH", dim="sector",
            score=70.0, detail={},
        ))
        # 今日 limit：会被 replace 清掉
        session.add(FilterScoreDaily(
            trade_date=td_today, ts_code="A.SH", dim="limit",
            score=60.0, detail={"sector_heat_bonus": 60},
        ))
        session.commit()

        replace_filter_scores(
            session, td_today,
            dims=["limit"],
            results=[],  # 本轮无产出
        )
        session.commit()

        # 昨日 limit、今日 sector 都还在；今日 limit 被清空
        assert session.query(FilterScoreDaily).filter_by(
            trade_date=td_yesterday, dim="limit",
        ).count() == 1
        assert session.query(FilterScoreDaily).filter_by(
            trade_date=td_today, dim="sector",
        ).count() == 1
        assert session.query(FilterScoreDaily).filter_by(
            trade_date=td_today, dim="limit",
        ).count() == 0

        session.close()
        engine.dispose()


class TestSelectionResultRefresh:
    """v2.3：combine_scores 必须每日全量替换 selection_result，不残留旧入选股。

    场景：旧版 sector_heat_bonus 让 GHOST.SH 入选 picked=True；v2.3 删除该机制
    + replace_filter_scores 清掉脏分数，本轮 GHOST.SH 已无任何维度分数 → 不会
    出现在 combine 的 rows 里。但若仅 upsert，selection_result 里的旧 picked=True
    会残留 → 报告渲染（仅查 picked=True）就会污染。
    """

    def test_stale_picked_does_not_survive_rerun(self):
        _register_jsonb_on_sqlite()

        from mo_stock.filters.base import ScoreResult
        from mo_stock.scorer.combine import (
            combine_scores,
            replace_filter_scores,
        )
        from mo_stock.storage.models import (
            Base,
            IndexMember,
            SelectionResult,
            StockBasic,
        )

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
        session = Session()

        trade_date = date(2026, 4, 27)

        # 准备 1 只新版本能算分的活股 + 1 只"幽灵股"（旧版入选，新版无分）
        for code in ["LIVE.SH", "GHOST.SH"]:
            session.add(StockBasic(
                ts_code=code, symbol=code.split(".")[0], name=code,
                is_st=False, list_date=date(2000, 1, 1),
            ))
            session.add(IndexMember(ts_code=code, l1_code="801080.SI"))
        session.commit()

        # 旧版 selection_result 残留：GHOST 入选 picked=True
        session.add(SelectionResult(
            trade_date=trade_date, ts_code="GHOST.SH",
            rank=1, rule_score=60.0, ai_score=80.0, final_score=68.0,
            picked=True, reject_reason=None,
        ))
        # 也加一条 LIVE 的旧记录（rank 不同，验证 rank 也被刷新）
        session.add(SelectionResult(
            trade_date=trade_date, ts_code="LIVE.SH",
            rank=20, rule_score=10.0, ai_score=None, final_score=10.0,
            picked=True, reject_reason=None,
        ))
        session.commit()

        # 新版本本轮：只有 LIVE 有分数，GHOST 完全没有维度行
        replace_filter_scores(
            session, trade_date,
            dims=["limit", "moneyflow", "lhb", "sector", "theme"],
            results=[
                ScoreResult(
                    ts_code="LIVE.SH", trade_date=trade_date, dim="limit",
                    score=50.0, detail={},
                ),
            ],
        )

        combine_scores(
            session, trade_date,
            dimension_weights={"limit": 1.0},
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20,
            enable_ai=False,
            combine_cfg={"max_stocks_per_sector": 4},
        )

        # 关键断言：
        # 1) GHOST 旧 picked=True 必须消失（要么不存在，要么 picked=False）
        ghost = session.query(SelectionResult).filter_by(
            trade_date=trade_date, ts_code="GHOST.SH",
        ).one_or_none()
        assert ghost is None or ghost.picked is False, (
            f"GHOST 旧入选记录残留：{ghost.__dict__ if ghost else None}"
        )

        # 2) LIVE 是本轮唯一入选，rank 应被刷新为 1
        live = session.query(SelectionResult).filter_by(
            trade_date=trade_date, ts_code="LIVE.SH",
        ).one()
        assert live.picked is True
        assert live.rank == 1
        assert live.rule_score == 50.0

        # 3) 报告查询路径（picked=True）只返回 LIVE 一只
        picked_for_report = session.query(SelectionResult).filter_by(
            trade_date=trade_date, picked=True,
        ).all()
        assert {r.ts_code for r in picked_for_report} == {"LIVE.SH"}

        session.close()
        engine.dispose()

    def test_other_trade_dates_not_affected(self):
        """delete 当日时不能误删其它交易日的 selection_result。"""
        _register_jsonb_on_sqlite()

        from mo_stock.filters.base import ScoreResult
        from mo_stock.scorer.combine import (
            combine_scores,
            replace_filter_scores,
        )
        from mo_stock.storage.models import (
            Base,
            IndexMember,
            SelectionResult,
            StockBasic,
        )

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)  # noqa: N806
        session = Session()

        td_today = date(2026, 4, 27)
        td_yesterday = date(2026, 4, 26)

        session.add(StockBasic(
            ts_code="A.SH", symbol="A", name="A",
            is_st=False, list_date=date(2000, 1, 1),
        ))
        session.add(IndexMember(ts_code="A.SH", l1_code="801080.SI"))
        # 昨日的 selection_result（不能被今日 combine 误删）
        session.add(SelectionResult(
            trade_date=td_yesterday, ts_code="A.SH",
            rank=3, rule_score=40.0, ai_score=None, final_score=40.0,
            picked=True, reject_reason=None,
        ))
        session.commit()

        replace_filter_scores(
            session, td_today,
            dims=["limit"],
            results=[ScoreResult(
                ts_code="A.SH", trade_date=td_today, dim="limit",
                score=50.0, detail={},
            )],
        )
        combine_scores(
            session, td_today,
            dimension_weights={"limit": 1.0},
            hard_reject_cfg={
                "exclude_st": False, "min_list_days": 0,
                "exclude_today_limit_up": False,
                "exclude_today_limit_down": False,
                "exclude_suspended": False,
            },
            top_n=20, enable_ai=False,
        )

        yesterday_row = session.query(SelectionResult).filter_by(
            trade_date=td_yesterday, ts_code="A.SH",
        ).one()
        assert yesterday_row.picked is True
        assert yesterday_row.rank == 3
        assert yesterday_row.rule_score == 40.0

        session.close()
        engine.dispose()
