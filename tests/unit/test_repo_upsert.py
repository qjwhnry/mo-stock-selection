"""repo.upsert_rows 的幂等性 / 对齐性单测。

P0-9（审计 2026-04-26）：upsert 用 PG 方言 ON CONFLICT，依赖 conflict_cols
与表主键完全对齐。否则同一只股同一天重跑会要么撞约束、要么静默写入重复行。

本测试覆盖：
1. 所有便捷封装（upsert_daily_kline / upsert_lhb / upsert_sw_daily 等）
   传入的 conflict_cols **完全等于**对应表的主键列集合
2. 错误对齐时 upsert_rows 会立即 ValueError（防御断言生效）
3. 空 rows 时返回 0（无副作用）

不依赖真实 DB（PG / SQLite），跑得快且零依赖。
"""
from __future__ import annotations

import pytest

from mo_stock.storage import repo
from mo_stock.storage.models import (
    DailyBasic,
    DailyKline,
    IndexMember,
    Lhb,
    LimitList,
    Moneyflow,
    StockBasic,
    SwDaily,
    ThsIndex,
    ThsMember,
    TradeCal,
)

# (便捷封装函数, 模型类, 期望的 conflict_cols)
# 期望值直接对照 repo.py 中 upsert_xxx 函数的硬编码——任一改动都得改两边。
_UPSERT_BINDINGS = [
    (repo.upsert_daily_kline,   DailyKline,   ["ts_code", "trade_date"]),
    (repo.upsert_daily_basic,   DailyBasic,   ["ts_code", "trade_date"]),
    (repo.upsert_limit_list,    LimitList,    ["ts_code", "trade_date"]),
    (repo.upsert_moneyflow,     Moneyflow,    ["ts_code", "trade_date"]),
    (repo.upsert_lhb,           Lhb,          ["trade_date", "ts_code"]),
    (repo.upsert_index_member,  IndexMember,  ["ts_code"]),
    (repo.upsert_ths_index,     ThsIndex,     ["ts_code"]),
    (repo.upsert_ths_member,    ThsMember,    ["ts_code", "con_code"]),
    (repo.upsert_stock_basic,   StockBasic,   ["ts_code"]),
    (repo.upsert_trade_cal,     TradeCal,     ["cal_date"]),
    (repo.upsert_sw_daily,      SwDaily,      ["sw_code", "trade_date"]),
]


@pytest.mark.parametrize(("upsert_fn", "model", "expected_cols"), _UPSERT_BINDINGS)
def test_conflict_cols_match_model_primary_key(
    upsert_fn: object, model: type, expected_cols: list[str],
) -> None:
    """每个便捷封装传入的 conflict_cols 必须等于模型主键列集合。

    保护意图：日后改主键（加列 / 拆分）时，若忘改 repo 封装，
    第一次 ingest 重跑就会撞 ValueError，立刻暴露。
    """
    pk_names = {c.name for c in model.__table__.primary_key.columns}
    assert set(expected_cols) == pk_names, (
        f"{upsert_fn.__name__} 传 {expected_cols}，但 {model.__name__} 主键是 {pk_names}"
    )


def test_upsert_rows_rejects_misaligned_conflict_cols() -> None:
    """P0-9 防御：conflict_cols 与主键不一致时立即 ValueError。

    我们手动构造一个错误的调用，session 设为 None（不会被使用，因为校验在前）。
    """
    with pytest.raises(ValueError, match="不一致"):
        repo.upsert_rows(
            session=None,  # type: ignore[arg-type]
            model=DailyKline,
            rows=[{"ts_code": "000001.SZ", "trade_date": "2026-04-26"}],
            conflict_cols=["ts_code"],  # 缺 trade_date，与 (ts_code, trade_date) PK 不一致
        )


def test_upsert_rows_empty_returns_zero() -> None:
    """空 rows 早返回 0，不触碰 session（即便 session 是 None 也不报错）。"""
    assert repo.upsert_rows(
        session=None,  # type: ignore[arg-type]
        model=DailyKline,
        rows=[],
        conflict_cols=["ts_code", "trade_date"],
    ) == 0
