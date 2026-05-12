"""LimitRestartFilter 单元测试。

覆盖最近一次涨停 T-2~T-5、T-1 排除、今日涨停跳过、
支撑/量能/惩罚项等关键口径。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from mo_stock.filters.short.limit_restart_filter import LimitRestartFilter
from mo_stock.storage.models import DailyKline, LimitList, StockBasic, TradeCal


def _add_trade_days(sqlite_session, days: list[date]) -> None:
    prev: date | None = None
    for day in days:
        sqlite_session.add(TradeCal(cal_date=day, is_open=True, pretrade_date=prev))
        prev = day


def _add_stock(sqlite_session, ts_code: str = "000001.SZ") -> None:
    sqlite_session.add(
        StockBasic(
            ts_code=ts_code,
            symbol=ts_code.split(".")[0],
            name="平安银行",
            industry="银行",
            sw_l1="银行",
            list_date=date(1991, 4, 3),
            is_st=False,
        )
    )


def _add_kline(
    sqlite_session,
    ts_code: str,
    trade_date: date,
    *,
    open_: float = 10.3,
    high: float = 11.0,
    low: float = 10.2,
    close: float = 10.8,
    pre_close: float = 10.5,
    pct_chg: float = 2.86,
    vol: float = 300.0,
) -> None:
    sqlite_session.add(
        DailyKline(
            ts_code=ts_code,
            trade_date=trade_date,
            open=open_,
            high=high,
            low=low,
            close=close,
            pre_close=pre_close,
            pct_chg=pct_chg,
            vol=vol,
            amount=vol * close,
        )
    )


def _add_limit(sqlite_session, ts_code: str, trade_date: date) -> None:
    sqlite_session.add(
        LimitList(
            ts_code=ts_code,
            trade_date=trade_date,
            limit_type="U",
            fd_amount=50_000_000.0,
            first_time="09:45:00",
            last_time="14:55:00",
            open_times=0,
            up_stat="1/1",
            limit_times=1,
        )
    )


def _seed_restart_case(
    sqlite_session,
    *,
    today: date,
    limit_day: date,
    ts_code: str = "000001.SZ",
    today_vol: float = 300.0,
    limit_vol: float = 1000.0,
) -> None:
    days = [
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
        date(2026, 5, 11),
        date(2026, 5, 12),
    ]
    _add_trade_days(sqlite_session, days)
    _add_stock(sqlite_session, ts_code)

    for day in days:
        if day == limit_day:
            _add_kline(
                sqlite_session,
                ts_code,
                day,
                open_=9.5,
                high=10.0,
                low=9.5,
                close=10.0,
                pre_close=9.1,
                pct_chg=9.89,
                vol=limit_vol,
            )
        elif day == today:
            _add_kline(sqlite_session, ts_code, day, vol=today_vol)
        else:
            _add_kline(
                sqlite_session,
                ts_code,
                day,
                open_=10.1,
                high=10.4,
                low=10.1,
                close=10.2,
                pre_close=10.0,
                pct_chg=2.0,
                vol=200.0,
            )
    _add_limit(sqlite_session, ts_code, limit_day)


def _score_map(results):
    return {r.ts_code: r for r in results}


def test_t2_restart_scores(sqlite_session) -> None:
    today = date(2026, 5, 12)
    limit_day = date(2026, 5, 8)
    _seed_restart_case(sqlite_session, today=today, limit_day=limit_day)
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))["000001.SZ"]

    assert result.score == pytest.approx(90.0)
    assert result.detail["limit_date"] == "2026-05-08"
    assert result.detail["days_since_limit"] == 2
    assert result.detail["volume_vs_limit_day"] == pytest.approx(0.3)
    assert result.detail["support_level"] == "full"
    assert result.detail["volume_restore_score"] == 5


def test_t5_boundary_is_included(sqlite_session) -> None:
    today = date(2026, 5, 12)
    limit_day = date(2026, 5, 5)
    _seed_restart_case(sqlite_session, today=today, limit_day=limit_day)
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))["000001.SZ"]

    assert result.detail["days_since_limit"] == 5
    assert result.score > 0


def test_t1_limit_is_excluded(sqlite_session) -> None:
    today = date(2026, 5, 12)
    limit_day = date(2026, 5, 11)
    _seed_restart_case(sqlite_session, today=today, limit_day=limit_day)
    sqlite_session.commit()

    results = LimitRestartFilter().score_all(sqlite_session, today)

    assert "000001.SZ" not in _score_map(results)


def test_t3_and_t1_double_limit_uses_recent_limit_and_excludes(sqlite_session) -> None:
    today = date(2026, 5, 12)
    older_limit = date(2026, 5, 7)
    recent_limit = date(2026, 5, 11)
    _seed_restart_case(sqlite_session, today=today, limit_day=older_limit)
    _add_limit(sqlite_session, "000001.SZ", recent_limit)
    sqlite_session.commit()

    results = LimitRestartFilter().score_all(sqlite_session, today)

    assert "000001.SZ" not in _score_map(results)


def test_today_limit_up_is_skipped(sqlite_session) -> None:
    today = date(2026, 5, 12)
    _seed_restart_case(sqlite_session, today=today, limit_day=date(2026, 5, 8))
    _add_limit(sqlite_session, "000001.SZ", today)
    sqlite_session.commit()

    results = LimitRestartFilter().score_all(sqlite_session, today)

    assert "000001.SZ" not in _score_map(results)


def test_min_low_since_limit_blocks_support_score(sqlite_session) -> None:
    today = date(2026, 5, 12)
    _seed_restart_case(sqlite_session, today=today, limit_day=date(2026, 5, 8))
    # T-1 中间破掉涨停日 open=9.5，即使今日拉回也不应拿支撑分。
    sqlite_session.merge(
        DailyKline(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 11),
            open=9.8,
            high=10.2,
            low=9.4,
            close=10.1,
            pre_close=10.0,
            pct_chg=1.0,
            vol=200.0,
            amount=2020.0,
        )
    )
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))["000001.SZ"]

    assert result.detail["support_level"] == "broken"
    assert result.detail["support_score"] == 0


def test_abnormal_limit_day_volume_penalty(sqlite_session) -> None:
    today = date(2026, 5, 12)
    limit_day = date(2026, 5, 8)
    ts_code = "000001.SZ"
    pre_days = [limit_day - timedelta(days=i) for i in range(20, 0, -1)]
    days = [*pre_days, limit_day, date(2026, 5, 11), today]
    _add_trade_days(sqlite_session, days)
    _add_stock(sqlite_session, ts_code)
    for day in pre_days:
        _add_kline(sqlite_session, ts_code, day, vol=100.0)
    _add_kline(
        sqlite_session,
        ts_code,
        limit_day,
        open_=9.5,
        high=10.0,
        low=9.5,
        close=10.0,
        pre_close=9.1,
        pct_chg=9.89,
        vol=400.0,
    )
    _add_kline(sqlite_session, ts_code, date(2026, 5, 11), vol=200.0)
    _add_kline(sqlite_session, ts_code, today, vol=120.0)
    _add_limit(sqlite_session, ts_code, limit_day)
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))[ts_code]

    assert result.detail["limit_day_volume_vs_prev20"] == pytest.approx(4.0)
    assert result.detail["abnormal_limit_volume_penalty"] == 20


def test_multi_limit_penalty(sqlite_session) -> None:
    today = date(2026, 5, 12)
    _seed_restart_case(sqlite_session, today=today, limit_day=date(2026, 5, 8))
    _add_limit(sqlite_session, "000001.SZ", date(2026, 5, 6))
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))["000001.SZ"]

    assert result.detail["limit_count_5d"] == 2
    assert result.detail["multi_limit_penalty"] == 15


def test_zero_range_close_position_scores_zero(sqlite_session) -> None:
    today = date(2026, 5, 12)
    _seed_restart_case(sqlite_session, today=today, limit_day=date(2026, 5, 8))
    sqlite_session.merge(
        DailyKline(
            ts_code="000001.SZ",
            trade_date=today,
            open=10.5,
            high=10.5,
            low=10.5,
            close=10.5,
            pre_close=10.4,
            pct_chg=0.96,
            vol=300.0,
            amount=3150.0,
        )
    )
    sqlite_session.commit()

    result = _score_map(LimitRestartFilter().score_all(sqlite_session, today))["000001.SZ"]

    assert result.detail["close_position_score"] == 0
