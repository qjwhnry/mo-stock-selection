"""ingest_daily 模块的字段清洗工具测试。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from mo_stock.ingest.ingest_daily import _is_st, _nf, _ni, _parse_date, _str_or_none


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
