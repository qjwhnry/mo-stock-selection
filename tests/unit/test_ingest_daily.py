"""ingest_daily 模块的字段清洗工具测试。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from mo_stock.ingest.ingest_daily import (
    _dedupe_keep_latest_in_date,
    _index_member_rows_from_df,
    _is_st,
    _lhb_rows_from_df,
    _nf,
    _ni,
    _parse_date,
    _str_or_none,
    _sw_daily_rows_from_df,
    _ths_index_rows_from_df,
    _ths_member_rows_from_df,
)


class TestParseDate:
    def test_tushare_string_format(self) -> None:
        assert _parse_date("20260422") == date(2026, 4, 22)

    def test_date_object_passthrough(self) -> None:
        d = date(2026, 4, 22)
        assert _parse_date(d) == d

    def test_none_returns_none(self) -> None:
        assert _parse_date(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_date("") is None

    def test_nan_float(self) -> None:
        assert _parse_date(float("nan")) is None

    def test_literal_nan_string(self) -> None:
        assert _parse_date("nan") is None


class TestNumericCleanup:
    def test_nf_normal_float(self) -> None:
        assert _nf(3.14) == 3.14

    def test_nf_none(self) -> None:
        assert _nf(None) is None

    def test_nf_nan(self) -> None:
        assert _nf(float("nan")) is None

    def test_nf_pandas_na(self) -> None:
        # pandas 的 NaT / NA 也要被当成 None
        assert _nf(pd.NA) is None

    def test_nf_string_number(self) -> None:
        assert _nf("3.14") == 3.14

    def test_nf_non_numeric_string(self) -> None:
        assert _nf("abc") is None

    def test_ni_rounds_to_int(self) -> None:
        assert _ni(3.7) == 3

    def test_ni_none(self) -> None:
        assert _ni(None) is None


class TestStringCleanup:
    def test_str_or_none_normal(self) -> None:
        assert _str_or_none("abc") == "abc"

    def test_str_or_none_strips(self) -> None:
        assert _str_or_none("  hello  ") == "hello"

    def test_str_or_none_empty(self) -> None:
        assert _str_or_none("") is None
        assert _str_or_none("   ") is None

    def test_str_or_none_nan(self) -> None:
        assert _str_or_none(float("nan")) is None


class TestIsSt:
    def test_pure_st_prefix(self) -> None:
        assert _is_st("ST 中安") is True

    def test_star_st_prefix(self) -> None:
        assert _is_st("*ST 中安") is True

    def test_lowercase_st(self) -> None:
        assert _is_st("st 中安") is True

    def test_normal_stock(self) -> None:
        assert _is_st("贵州茅台") is False

    def test_empty_string(self) -> None:
        assert _is_st("") is False


class TestLhbRowsFromDf:
    """_lhb_rows_from_df：把 Tushare top_list DataFrame 映射成 Lhb 表的 row dict 列表。"""

    def test_empty_df_returns_empty_list(self) -> None:
        # 空 DataFrame（无行）应直接得到空列表，不抛异常
        empty = pd.DataFrame(columns=["trade_date", "ts_code"])
        assert _lhb_rows_from_df(empty) == []

    def test_dedupes_same_stock_same_day(self) -> None:
        """Tushare top_list 对同一只股票同一天可能返回多行（不同上榜原因），
        而 Lhb 表 PK 是 (trade_date, ts_code)，所以必须 dedupe，否则
        ON CONFLICT DO UPDATE 会抛 CardinalityViolation。
        """
        df = pd.DataFrame([
            {  # 第一次上榜原因：日涨幅偏离值达 7%
                "trade_date": "20260422",
                "ts_code": "600519.SH",
                "name": "贵州茅台",
                "close": 1680.0,
                "pct_change": 7.12,
                "turnover_rate": 1.2,
                "amount": 985000.0,
                "l_sell": 12000.0,
                "l_buy": 35000.0,
                "l_amount": 47000.0,
                "net_amount": 23000.0,
                "reason": "日涨幅偏离值达 7%",
            },
            {  # 同股同日另一上榜原因：3 日涨幅达 20%
                "trade_date": "20260422",
                "ts_code": "600519.SH",
                "name": "贵州茅台",
                "close": 1680.0,
                "pct_change": 7.12,
                "turnover_rate": 1.2,
                "amount": 985000.0,
                "l_sell": 8000.0,
                "l_buy": 30000.0,
                "l_amount": 38000.0,
                "net_amount": 22000.0,
                "reason": "3 日涨幅达 20%",
            },
            {  # 不同股，不应被误删
                "trade_date": "20260422",
                "ts_code": "300750.SZ",
                "name": "宁德时代",
                "close": 250.0,
                "pct_change": 8.5,
                "turnover_rate": 3.5,
                "amount": 600000.0,
                "l_sell": 5000.0,
                "l_buy": 25000.0,
                "l_amount": 30000.0,
                "net_amount": 20000.0,
                "reason": "日涨幅偏离值达 7%",
            },
        ])

        rows = _lhb_rows_from_df(df)

        # 同股同日两行应去重为一行；另一只股保留 → 共 2 行
        assert len(rows) == 2
        keys = {(r["trade_date"], r["ts_code"]) for r in rows}
        assert keys == {(date(2026, 4, 22), "600519.SH"), (date(2026, 4, 22), "300750.SZ")}

    def test_maps_all_lhb_fields(self) -> None:
        # 单行典型样本：覆盖所有会进库的字段（含 net_rate / amount_rate）
        df = pd.DataFrame([{
            "trade_date": "20260422",
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "close": 1680.5,
            "pct_change": 7.12,
            "turnover_rate": 1.23,
            "amount": 985000.0,
            "l_sell": 12000.0,
            "l_buy": 35000.0,
            "l_amount": 47000.0,
            "net_amount": 23000.0,
            "net_rate": 2.33,
            "amount_rate": 4.77,
            "float_values": 100000.0,  # 不入库
            "reason": "日涨幅偏离值达 7%",
        }])

        rows = _lhb_rows_from_df(df)

        assert len(rows) == 1
        r = rows[0]
        # trade_date 是 Tushare 的 'YYYYMMDD' 字符串，必须被转成 date 对象
        assert r["trade_date"] == date(2026, 4, 22)
        assert r["ts_code"] == "600519.SH"
        assert r["name"] == "贵州茅台"
        assert r["close"] == 1680.5
        assert r["pct_change"] == 7.12
        assert r["turnover_rate"] == 1.23
        assert r["amount"] == 985000.0
        assert r["l_sell"] == 12000.0
        assert r["l_buy"] == 35000.0
        assert r["l_amount"] == 47000.0
        assert r["net_amount"] == 23000.0
        # 新增：占比字段必须被映射进 row
        assert r["net_rate"] == 2.33
        assert r["amount_rate"] == 4.77
        assert r["reason"] == "日涨幅偏离值达 7%"
        assert r["seat"] is None
        # float_values 不入库
        assert "float_values" not in r


class TestSwDailyRowsFromDf:
    """_sw_daily_rows_from_df：Tushare sw_daily 的 ts_code 必须重命名成模型的 sw_code。"""

    def test_empty_df_returns_empty_list(self) -> None:
        empty = pd.DataFrame(columns=["ts_code", "trade_date"])
        assert _sw_daily_rows_from_df(empty) == []

    def test_renames_ts_code_to_sw_code_and_maps_official_fields(self) -> None:
        """字段对齐 Tushare 官方 sw_daily 接口（doc_id=327）：

        - 重命名 ts_code → sw_code
        - 不再有 turnover_rate（接口实际不返回此字段）
        - 新增映射 change / pe / pb / float_mv / total_mv
        """
        df = pd.DataFrame([{
            "ts_code": "801080.SI",
            "name": "电子",
            "trade_date": "20260422",
            "open": 4500.0,
            "high": 4620.0,
            "low": 4490.0,
            "close": 4600.0,
            "change": 100.0,
            "pct_change": 2.22,
            "vol": 1000000.0,
            "amount": 500000.0,
            "pe": 35.5,
            "pb": 3.8,
            "float_mv": 280000.0,
            "total_mv": 350000.0,
        }])

        rows = _sw_daily_rows_from_df(df)

        assert len(rows) == 1
        r = rows[0]
        assert r["sw_code"] == "801080.SI"
        assert "ts_code" not in r
        assert "turnover_rate" not in r  # 接口不返回，不应混入 row dict
        assert r["trade_date"] == date(2026, 4, 22)
        assert r["name"] == "电子"
        assert r["open"] == 4500.0
        assert r["high"] == 4620.0
        assert r["low"] == 4490.0
        assert r["close"] == 4600.0
        assert r["change"] == 100.0
        assert r["pct_change"] == 2.22
        assert r["vol"] == 1000000.0
        assert r["amount"] == 500000.0
        assert r["pe"] == 35.5
        assert r["pb"] == 3.8
        assert r["float_mv"] == 280000.0
        assert r["total_mv"] == 350000.0


class TestIndexMemberRowsFromDf:
    """_index_member_rows_from_df：Tushare index_member_all → IndexMember row。

    申万行业分类一股归属唯一，PK 用 ts_code。只入库 is_new='Y' 的最新成分。
    """

    def test_empty_df_returns_empty_list(self) -> None:
        empty = pd.DataFrame(columns=["ts_code", "l1_code"])
        assert _index_member_rows_from_df(empty) == []

    def test_maps_three_level_taxonomy(self) -> None:
        # 一股一行：l1/l2/l3 三级行业 + 纳入日期都要落库
        df = pd.DataFrame([{
            "l1_code": "801080.SI",
            "l1_name": "电子",
            "l2_code": "801083.SI",
            "l2_name": "半导体",
            "l3_code": "801087.SI",
            "l3_name": "数字芯片设计",
            "ts_code": "688981.SH",
            "name": "中芯国际",
            "in_date": "20200716",
            "out_date": None,  # 仍在该板块
            "is_new": "Y",
        }])

        rows = _index_member_rows_from_df(df)

        assert len(rows) == 1
        r = rows[0]
        assert r["ts_code"] == "688981.SH"
        assert r["l1_code"] == "801080.SI"
        assert r["l1_name"] == "电子"
        assert r["l2_code"] == "801083.SI"
        assert r["l2_name"] == "半导体"
        assert r["l3_code"] == "801087.SI"
        assert r["l3_name"] == "数字芯片设计"
        assert r["in_date"] == date(2020, 7, 16)
        # out_date / is_new 不入库（前者一直 NULL，后者一直 'Y'）
        assert "out_date" not in r
        assert "is_new" not in r
        assert "name" not in r  # 股票名已在 stock_basic 表里，不重复存

    def test_dedupes_same_ts_code(self) -> None:
        """某些 Tushare 镜像（如 101.35.233.113:8020）会把同一行复制多份返回，
        所有列完全一致。IndexMember PK 是 ts_code，必须去重，否则 ON CONFLICT
        DO UPDATE batch 里同 PK 撞 2 次会抛 CardinalityViolation。
        """
        df = pd.DataFrame([
            {  # 镜像 bug：同一条记录被复制了 3 份
                "l1_code": "801010.SI", "l1_name": "农林牧渔",
                "l2_code": "801016.SI", "l2_name": "种植业",
                "l3_code": "850111.SI", "l3_name": "种子",
                "ts_code": "000998.SZ", "name": "隆平高科",
                "in_date": "20000629", "out_date": None, "is_new": "Y",
            },
            {
                "l1_code": "801010.SI", "l1_name": "农林牧渔",
                "l2_code": "801016.SI", "l2_name": "种植业",
                "l3_code": "850111.SI", "l3_name": "种子",
                "ts_code": "000998.SZ", "name": "隆平高科",
                "in_date": "20000629", "out_date": None, "is_new": "Y",
            },
            {
                "l1_code": "801010.SI", "l1_name": "农林牧渔",
                "l2_code": "801016.SI", "l2_name": "种植业",
                "l3_code": "850111.SI", "l3_name": "种子",
                "ts_code": "000998.SZ", "name": "隆平高科",
                "in_date": "20000629", "out_date": None, "is_new": "Y",
            },
            {  # 不同股，应保留
                "l1_code": "801010.SI", "l1_name": "农林牧渔",
                "l2_code": "801016.SI", "l2_name": "种植业",
                "l3_code": "850111.SI", "l3_name": "种子",
                "ts_code": "000713.SZ", "name": "丰乐种业",
                "in_date": "19970422", "out_date": None, "is_new": "Y",
            },
        ])

        rows = _index_member_rows_from_df(df)

        assert len(rows) == 2
        ts_codes = {r["ts_code"] for r in rows}
        assert ts_codes == {"000998.SZ", "000713.SZ"}

    def test_filters_out_non_latest_records(self) -> None:
        """如果接口意外返回了 is_new='N' 的旧记录，必须过滤掉，
        否则同 ts_code 不同板块归属会触发 PK 冲突。
        """
        df = pd.DataFrame([
            {  # 旧归属：已被剔除
                "l1_code": "801080.SI", "l1_name": "电子",
                "l2_code": "801081.SI", "l2_name": "半导体材料",
                "l3_code": "801082.SI", "l3_name": "光刻胶",
                "ts_code": "688981.SH", "name": "中芯国际",
                "in_date": "20180101", "out_date": "20200715", "is_new": "N",
            },
            {  # 当前最新归属
                "l1_code": "801080.SI", "l1_name": "电子",
                "l2_code": "801083.SI", "l2_name": "半导体",
                "l3_code": "801087.SI", "l3_name": "数字芯片设计",
                "ts_code": "688981.SH", "name": "中芯国际",
                "in_date": "20200716", "out_date": None, "is_new": "Y",
            },
        ])

        rows = _index_member_rows_from_df(df)

        assert len(rows) == 1
        assert rows[0]["l3_code"] == "801087.SI"  # 留下当前最新


class TestDedupeKeepLatestInDate:
    """_dedupe_keep_latest_in_date：跨 l1 拼接后按 in_date 最新优先去重。

    场景：申万 2026-03-05 评审把 600185 从房地产改为旅游零售，但 Tushare 旧记录的
    out_date 没标记，导致同一只股出现在两个一级行业。取 in_date 最新 = 跟到申万最新归属。
    """

    def test_empty_input(self) -> None:
        assert _dedupe_keep_latest_in_date([]) == []

    def test_no_duplicates_returns_all(self) -> None:
        rows = [
            {"ts_code": "000001.SZ", "in_date": date(2020, 1, 1), "l1_code": "801780.SI"},
            {"ts_code": "000002.SZ", "in_date": date(2020, 1, 1), "l1_code": "801180.SI"},
        ]
        out = _dedupe_keep_latest_in_date(rows)
        ts = {r["ts_code"] for r in out}
        assert ts == {"000001.SZ", "000002.SZ"}

    def test_keeps_latest_in_date_for_duplicates(self) -> None:
        # 模拟 600185 跨 l1 重复：旧归属 1999 房地产、新归属 2026 商贸零售
        rows = [
            {  # 旧归属 in_date 早
                "ts_code": "600185.SH",
                "in_date": date(1999, 6, 11),
                "l1_code": "801180.SI",
                "l1_name": "房地产",
            },
            {  # 新归属 in_date 晚 ← 应该被保留
                "ts_code": "600185.SH",
                "in_date": date(2026, 3, 5),
                "l1_code": "801200.SI",
                "l1_name": "商贸零售",
            },
        ]
        out = _dedupe_keep_latest_in_date(rows)
        assert len(out) == 1
        assert out[0]["l1_code"] == "801200.SI"
        assert out[0]["in_date"] == date(2026, 3, 5)

    def test_treats_none_in_date_as_oldest(self) -> None:
        # in_date=None 视为最旧，被任何有日期的记录覆盖
        rows = [
            {"ts_code": "000001.SZ", "in_date": None, "l1_code": "801780.SI"},
            {"ts_code": "000001.SZ", "in_date": date(2020, 1, 1), "l1_code": "801880.SI"},
        ]
        out = _dedupe_keep_latest_in_date(rows)
        assert len(out) == 1
        assert out[0]["l1_code"] == "801880.SI"  # 有日期的优先

    def test_preserves_input_when_all_none_in_date(self) -> None:
        # 全 None 的 in_date：随便保留一条即可（不应崩溃）
        rows = [
            {"ts_code": "000001.SZ", "in_date": None, "l1_code": "801780.SI"},
            {"ts_code": "000001.SZ", "in_date": None, "l1_code": "801880.SI"},
        ]
        out = _dedupe_keep_latest_in_date(rows)
        assert len(out) == 1


class TestThsIndexRowsFromDf:
    """_ths_index_rows_from_df：Tushare ths_index → ThsIndex row。

    同花顺概念板块元数据。PK=ts_code。注意：ts_code 在 ths_index 是概念代码（如
    885328.TI），跟 stock_basic.ts_code（个股代码）含义不同。
    """

    def test_empty_df_returns_empty_list(self) -> None:
        empty = pd.DataFrame(columns=["ts_code"])
        assert _ths_index_rows_from_df(empty) == []

    def test_maps_all_ths_index_fields(self) -> None:
        df = pd.DataFrame([{
            "ts_code": "885328.TI",
            "name": "新能源车",
            "count": 156,
            "exchange": "A",
            "list_date": "20180601",
            "type": "N",
        }])

        rows = _ths_index_rows_from_df(df)

        assert len(rows) == 1
        r = rows[0]
        assert r["ts_code"] == "885328.TI"
        assert r["name"] == "新能源车"
        assert r["count"] == 156
        assert r["exchange"] == "A"
        assert r["list_date"] == date(2018, 6, 1)
        assert r["type"] == "N"


class TestThsMemberRowsFromDf:
    """_ths_member_rows_from_df：Tushare ths_member → ThsMember row。

    成分映射：(ts_code=概念代码, con_code=股票代码) 联合 PK，一股可属多个概念。
    Tushare 镜像同样存在行复制 bug，需要按 (ts_code, con_code) 去重。
    """

    def test_empty_df_returns_empty_list(self) -> None:
        empty = pd.DataFrame(columns=["ts_code", "con_code"])
        assert _ths_member_rows_from_df(empty) == []

    def test_maps_all_ths_member_fields(self) -> None:
        # weight / in_date / out_date 接口暂无数据但字段保留
        df = pd.DataFrame([{
            "ts_code": "885328.TI",
            "con_code": "300750.SZ",
            "con_name": "宁德时代",
            "weight": None,
            "in_date": None,
            "out_date": None,
            "is_new": "Y",
        }])

        rows = _ths_member_rows_from_df(df)

        assert len(rows) == 1
        r = rows[0]
        assert r["ts_code"] == "885328.TI"
        assert r["con_code"] == "300750.SZ"
        assert r["con_name"] == "宁德时代"
        assert r["weight"] is None
        assert r["in_date"] is None
        assert r["out_date"] is None
        assert "is_new" not in r  # is_new 不入库（始终 'Y'）
        assert "con_name" in r  # con_name 保留：同股在不同概念里名字一样，但避免每次 join stock_basic

    def test_filters_non_latest_records(self) -> None:
        df = pd.DataFrame([
            {"ts_code": "885328.TI", "con_code": "300750.SZ", "con_name": "宁德时代",
             "weight": None, "in_date": None, "out_date": "20240101", "is_new": "N"},
            {"ts_code": "885328.TI", "con_code": "002594.SZ", "con_name": "比亚迪",
             "weight": None, "in_date": None, "out_date": None, "is_new": "Y"},
        ])

        rows = _ths_member_rows_from_df(df)

        assert len(rows) == 1
        assert rows[0]["con_code"] == "002594.SZ"

    def test_dedupes_same_concept_and_stock(self) -> None:
        """镜像 bug：同 (ts_code, con_code) 多行，保留首条。"""
        df = pd.DataFrame([
            {"ts_code": "885328.TI", "con_code": "300750.SZ", "con_name": "宁德时代",
             "weight": None, "in_date": None, "out_date": None, "is_new": "Y"},
            {"ts_code": "885328.TI", "con_code": "300750.SZ", "con_name": "宁德时代",
             "weight": None, "in_date": None, "out_date": None, "is_new": "Y"},
            {"ts_code": "885328.TI", "con_code": "002594.SZ", "con_name": "比亚迪",
             "weight": None, "in_date": None, "out_date": None, "is_new": "Y"},
        ])

        rows = _ths_member_rows_from_df(df)

        assert len(rows) == 2
        assert {r["con_code"] for r in rows} == {"300750.SZ", "002594.SZ"}
