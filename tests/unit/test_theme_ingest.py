"""题材增强数据清洗函数单测（v2.1 plan Task 3）。

覆盖：
- _ths_daily_rows_from_df：name 由 ths_index name_map 注入
- _limit_concept_rows_from_df：drop_duplicates 防 Tushare 镜像 bug
- _concept_moneyflow_rows_from_df：3 个净额字段都入库（v2.1 修法）
- _hm_list_rows_from_df / _hm_detail_rows_from_df
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from mo_stock.ingest.ingest_daily import (
    _concept_moneyflow_rows_from_df,
    _hm_detail_rows_from_df,
    _hm_list_rows_from_df,
    _limit_concept_rows_from_df,
    _ths_daily_rows_from_df,
)


class TestThsDailyRowsFromDf:
    def test_maps_fields_with_name_map(self) -> None:
        df = pd.DataFrame([{
            "ts_code": "885806.TI", "trade_date": "20260424",
            "close": 1000.5, "open": 990.0, "high": 1010.0, "low": 985.0,
            "pre_close": 985.0, "avg_price": 1000.0, "change": 15.5,
            "pct_change": 2.46, "vol": 100000.0, "turnover_rate": 3.2,
            "total_mv": 1_000_000.0, "float_mv": 800_000.0,
        }])
        rows = _ths_daily_rows_from_df(df, name_map={"885806.TI": "华为概念"})
        assert len(rows) == 1
        assert rows[0]["ts_code"] == "885806.TI"
        assert rows[0]["trade_date"] == date(2026, 4, 24)
        assert rows[0]["name"] == "华为概念"
        assert rows[0]["pct_change"] == 2.46
        assert rows[0]["turnover_rate"] == 3.2

    def test_empty_df_returns_empty_list(self) -> None:
        assert _ths_daily_rows_from_df(pd.DataFrame()) == []

    def test_missing_name_in_map_uses_none(self) -> None:
        df = pd.DataFrame([{
            "ts_code": "999999.TI", "trade_date": "20260424",
            "pct_change": 1.0,
        }])
        rows = _ths_daily_rows_from_df(df, name_map={})
        assert rows[0]["name"] is None


class TestLimitConceptRowsFromDf:
    def test_maps_fields(self) -> None:
        df = pd.DataFrame([{
            "ts_code": "885806.TI", "trade_date": "20260424",
            "name": "华为", "days": 3, "up_stat": "5/9",
            "cons_nums": 5, "up_nums": 9, "pct_chg": 4.5, "rank": 1,
        }])
        rows = _limit_concept_rows_from_df(df)
        assert len(rows) == 1
        assert rows[0]["rank"] == 1
        assert rows[0]["up_nums"] == 9

    def test_drop_duplicates_by_pk(self) -> None:
        """Tushare 镜像偶发同 (trade_date, ts_code) 重复，需 dedupe 防撞 PK。"""
        df = pd.DataFrame([
            {"ts_code": "885806.TI", "trade_date": "20260424", "rank": 1},
            {"ts_code": "885806.TI", "trade_date": "20260424", "rank": 2},  # 重复
        ])
        rows = _limit_concept_rows_from_df(df)
        assert len(rows) == 1
        assert rows[0]["rank"] == 1  # keep='first'


class TestConceptMoneyflowRowsFromDf:
    def test_keeps_all_three_amounts(self) -> None:
        """v2.1 关键修法：3 个净额字段都要入库，不能像 v1 只存 net_amount。"""
        df = pd.DataFrame([{
            "ts_code": "885806.TI", "trade_date": "20260424",
            "name": "华为", "lead_stock": "600000.SH", "pct_change": 5.0,
            "company_num": 50, "pct_change_stock": 9.99,
            "net_buy_amount": 12.3, "net_sell_amount": 5.6, "net_amount": 6.7,
        }])
        rows = _concept_moneyflow_rows_from_df(df)
        assert rows[0]["net_buy_amount"] == 12.3
        assert rows[0]["net_sell_amount"] == 5.6
        assert rows[0]["net_amount"] == 6.7

    def test_empty_df(self) -> None:
        assert _concept_moneyflow_rows_from_df(pd.DataFrame()) == []


class TestHotMoneyRowsFromDf:
    def test_hm_list(self) -> None:
        df = pd.DataFrame([{
            "name": "赵老哥", "desc": "经验丰富的游资",
            "orgs": "中信证券上海溧阳路营业部, 华泰证券深圳益田路营业部",
        }])
        rows = _hm_list_rows_from_df(df)
        assert rows[0]["name"] == "赵老哥"
        assert "溧阳路" in rows[0]["orgs"]

    def test_hm_detail_single_row(self) -> None:
        df = pd.DataFrame([{
            "trade_date": "20260424", "ts_code": "600000.SH",
            "ts_name": "浦发银行", "buy_amount": 1e7, "sell_amount": 1e6,
            "net_amount": 9e6, "hm_name": "赵老哥",
            "hm_orgs": "中信证券上海溧阳路营业部", "tag": "买入",
        }])
        rows = _hm_detail_rows_from_df(df)
        assert rows[0]["trade_date"] == date(2026, 4, 24)
        assert rows[0]["hm_name"] == "赵老哥"
        assert rows[0]["net_amount"] == 9e6

    def test_hm_detail_aggregates_multiple_orgs(self) -> None:
        """方案 B：同 (date, ts_code, hm_name) 多营业部 → 1 行聚合（金额 sum + 营业部拼接）。"""
        df = pd.DataFrame([
            {"trade_date": "20260424", "ts_code": "000062.SZ",
             "ts_name": "深圳华强", "hm_name": "小棉袄",
             "buy_amount": 3_000_000, "sell_amount": 0, "net_amount": 3_000_000,
             "hm_orgs": "东方财富证券拉萨", "tag": "买入"},
            {"trade_date": "20260424", "ts_code": "000062.SZ",
             "ts_name": "深圳华强", "hm_name": "小棉袄",
             "buy_amount": 2_000_000, "sell_amount": 0, "net_amount": 2_000_000,
             "hm_orgs": "华林证券深圳益田路", "tag": "买入"},
            {"trade_date": "20260424", "ts_code": "000062.SZ",
             "ts_name": "深圳华强", "hm_name": "小棉袄",
             "buy_amount": 1_030_000, "sell_amount": 0, "net_amount": 1_030_000,
             "hm_orgs": "银河证券嘉兴中环南路", "tag": "买入"},
        ])
        rows = _hm_detail_rows_from_df(df)
        assert len(rows) == 1
        r = rows[0]
        # 金额求和
        assert r["buy_amount"] == 6_030_000
        assert r["sell_amount"] == 0
        assert r["net_amount"] == 6_030_000
        # 营业部 ; 拼接保留全部，去重 + 排序保证幂等
        assert r["hm_orgs"].count(";") == 2
        assert "东方财富证券拉萨" in r["hm_orgs"]
        assert "华林证券深圳益田路" in r["hm_orgs"]
        assert "银河证券嘉兴中环南路" in r["hm_orgs"]

    def test_hm_detail_dedupe_within_aggregation(self) -> None:
        """同营业部多次返回也只在 hm_orgs 出现一次（去重）。"""
        df = pd.DataFrame([
            {"trade_date": "20260424", "ts_code": "000062.SZ", "ts_name": "深圳华强",
             "hm_name": "X", "buy_amount": 100, "sell_amount": 0, "net_amount": 100,
             "hm_orgs": "A 营业部", "tag": "买入"},
            {"trade_date": "20260424", "ts_code": "000062.SZ", "ts_name": "深圳华强",
             "hm_name": "X", "buy_amount": 100, "sell_amount": 0, "net_amount": 100,
             "hm_orgs": "A 营业部", "tag": "买入"},  # 重复
        ])
        rows = _hm_detail_rows_from_df(df)
        assert len(rows) == 1
        assert rows[0]["buy_amount"] == 200  # sum 仍叠加金额
        assert rows[0]["hm_orgs"] == "A 营业部"  # 营业部只出现一次

    def test_hm_detail_handles_null_orgs(self) -> None:
        """hm_orgs 为 NaN/空时聚合不报错，hm_orgs 字段为 None。"""
        import numpy as np
        df = pd.DataFrame([
            {"trade_date": "20260424", "ts_code": "X", "ts_name": "test",
             "hm_name": "Y", "buy_amount": 1, "sell_amount": 0, "net_amount": 1,
             "hm_orgs": np.nan, "tag": None},
        ])
        rows = _hm_detail_rows_from_df(df)
        assert len(rows) == 1
        assert rows[0]["hm_orgs"] is None
