"""龙虎榜席位明细清洗与分类单测（v2.1 plan Task 3）。

覆盖：
- _classify_seat：institution / northbound / hot_money / other（无 quant_like）
- _split_orgs_string：从 hm_list.orgs 拆分号/逗号 → set
- _stable_seat_key / _top_inst_rows_from_df：稳定 sha1 key 防止顺序覆盖
"""
from __future__ import annotations

import pandas as pd

from mo_stock.ingest.ingest_daily import (
    _classify_seat,
    _split_orgs_string,
    _stable_seat_key,
    _top_inst_rows_from_df,
)


class TestClassifySeat:
    def test_institution(self) -> None:
        assert _classify_seat("机构专用", set()) == "institution"

    def test_northbound_sh(self) -> None:
        assert _classify_seat("沪股通专用", set()) == "northbound"

    def test_northbound_sz(self) -> None:
        assert _classify_seat("深股通专用", set()) == "northbound"

    def test_hot_money_exact_match(self) -> None:
        """完全相等匹配，避免子串误判。"""
        orgs = {"中信证券上海溧阳路营业部"}
        assert _classify_seat("中信证券上海溧阳路营业部", orgs) == "hot_money"
        # 不同营业部不匹配，即使共享前缀
        assert _classify_seat("中信证券北京东直门外大街营业部", orgs) == "other"

    def test_no_quant_like_classification(self) -> None:
        """v2.1 修法：删除 quant_like 启发式（"华鑫证券" 不再被特殊标记）。"""
        assert _classify_seat("华鑫证券上海某营业部", set()) == "other"
        assert _classify_seat("某量化席位", set()) == "other"

    def test_empty_or_none(self) -> None:
        assert _classify_seat(None, set()) == "other"
        assert _classify_seat("", set()) == "other"
        assert _classify_seat("   ", set()) == "other"


class TestSplitOrgsString:
    def test_splits_chinese_and_english_separators(self) -> None:
        raw = "中信证券上海溧阳路营业部,华泰证券深圳益田路营业部;东方财富证券拉萨；招商证券北京"
        parts = _split_orgs_string(raw)
        assert "中信证券上海溧阳路营业部" in parts
        assert "华泰证券深圳益田路营业部" in parts
        assert "东方财富证券拉萨" in parts
        assert "招商证券北京" in parts

    def test_strips_whitespace(self) -> None:
        parts = _split_orgs_string("  中信证券上海溧阳路营业部 , 华泰证券  ")
        assert parts == {"中信证券上海溧阳路营业部", "华泰证券"}

    def test_none_or_empty(self) -> None:
        assert _split_orgs_string(None) == set()
        assert _split_orgs_string("") == set()


class TestStableSeatKey:
    def test_deterministic(self) -> None:
        """相同输入 → 相同 sha1。"""
        k1 = _stable_seat_key("600000.SH", "机构专用", "0", "日涨幅偏离值达7%")
        k2 = _stable_seat_key("600000.SH", "机构专用", "0", "日涨幅偏离值达7%")
        assert k1 == k2

    def test_distinct_for_different_seats(self) -> None:
        k_inst = _stable_seat_key("600000.SH", "机构专用", "0", "理由")
        k_hm = _stable_seat_key("600000.SH", "中信证券某营业部", "0", "理由")
        assert k_inst != k_hm


class TestTopInstRowsFromDf:
    def test_assigns_seat_no_and_type(self) -> None:
        df = pd.DataFrame([
            {"trade_date": "20260424", "ts_code": "600000.SH",
             "exalter": "机构专用", "side": "0",
             "buy": 12_000_000.0, "sell": 2_000_000.0, "net_buy": 10_000_000.0,
             "reason": "日涨幅偏离值达7%的证券"},
            {"trade_date": "20260424", "ts_code": "600000.SH",
             "exalter": "中信证券上海溧阳路营业部", "side": "0",
             "buy": 5_000_000.0, "sell": 1_000_000.0, "net_buy": 4_000_000.0,
             "reason": "日涨幅偏离值达7%的证券"},
        ])
        rows = _top_inst_rows_from_df(df, hot_money_orgs={"中信证券上海溧阳路营业部"})
        assert len(rows) == 2
        # seat_no 1..N，按稳定排序后展示
        assert {r["seat_no"] for r in rows} == {1, 2}
        # 每行有 seat_key
        assert all(r["seat_key"] for r in rows)
        # 类型识别正确
        types = {r["exalter"]: r["seat_type"] for r in rows}
        assert types["机构专用"] == "institution"
        assert types["中信证券上海溧阳路营业部"] == "hot_money"

    def test_stable_keys_when_order_changes(self) -> None:
        """top_inst 返回顺序变化时，seat_key 集合必须稳定（避免 upsert 覆盖错行）。"""
        df = pd.DataFrame([
            {"trade_date": "20260424", "ts_code": "600000.SH",
             "exalter": "中信证券上海溧阳路营业部", "side": "0",
             "buy": 5_000_000.0, "sell": 1_000_000.0, "net_buy": 4_000_000.0,
             "reason": "日涨幅偏离值达7%的证券"},
            {"trade_date": "20260424", "ts_code": "600000.SH",
             "exalter": "机构专用", "side": "0",
             "buy": 12_000_000.0, "sell": 2_000_000.0, "net_buy": 10_000_000.0,
             "reason": "日涨幅偏离值达7%的证券"},
        ])
        rows_a = _top_inst_rows_from_df(df, hot_money_orgs={"中信证券上海溧阳路营业部"})
        rows_b = _top_inst_rows_from_df(
            df.iloc[::-1].reset_index(drop=True),
            hot_money_orgs={"中信证券上海溧阳路营业部"},
        )
        assert {r["seat_key"] for r in rows_a} == {r["seat_key"] for r in rows_b}

    def test_empty_df(self) -> None:
        assert _top_inst_rows_from_df(pd.DataFrame(), set()) == []
