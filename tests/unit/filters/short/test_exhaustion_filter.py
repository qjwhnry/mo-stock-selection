"""ExhaustionFilter 纯函数测试。

测试各惩罚函数的输入输出正确性。
"""
from __future__ import annotations

import pytest

from mo_stock.filters.short.exhaustion_filter import (
    _compute_ma5,
    _count_consecutive_up,
    _penalty_5d_return,
    _penalty_consecutive_up,
    _penalty_ma5_deviation,
    _penalty_momentum_decay,
    _penalty_upper_shadow,
    _penalty_volume_divergence,
)


def _kline(close: float, open_: float | None = None,
           high: float | None = None, low: float | None = None,
           vol: float | None = None) -> dict:
    """Helper：构造一条 K 线 dict。"""
    return {
        "close": close,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "vol": vol,
    }


def _klines(*closes: float) -> list[dict]:
    """Helper：连续 N 日 K 线，每日 open=close（平收），vol=None。"""
    return [_kline(c) for c in closes]


def _upper_shadow_klines(
    *,
    pre_close: float = 10.0,
    open_: float = 10.0,
    high: float = 10.8,
    low: float = 10.0,
    close: float = 10.15,
    close_3d_ago: float = 9.4,
) -> list[dict]:
    """Helper：构造满足冲高回落计算窗口的 4 条 K 线。"""
    return [
        _kline(close_3d_ago),
        _kline(9.7),
        _kline(pre_close),
        _kline(close, open_=open_, high=high, low=low),
    ]


# ---------- _compute_ma5 ----------


class TestComputeMA5:
    def test_normal(self) -> None:
        klines = _klines(10, 11, 12, 13, 14)
        assert _compute_ma5(klines) == 12.0

    def test_less_than_5_returns_none(self) -> None:
        klines = _klines(10, 11, 12)
        assert _compute_ma5(klines) is None


# ---------- _penalty_5d_return ----------

CFG_5D = {"penalty_5d_return_max": 35}

class TestPenalty5dReturn:
    def test_no_penalty_below_5pct(self) -> None:
        klines = _klines(100, 101, 102, 103, 104, 105)  # 5%
        assert _penalty_5d_return(klines, CFG_5D) == 0

    def test_linear_midpoint(self) -> None:
        # 15% return → (15-5)/20 * 35 = 17.5
        klines = _klines(100, 102, 105, 108, 111, 115)
        expected = (15 - 5) / 20 * 35
        assert _penalty_5d_return(klines, CFG_5D) == pytest.approx(expected)

    def test_max_penalty_at_25pct(self) -> None:
        klines = _klines(100, 105, 110, 115, 120, 125)  # 25%
        assert _penalty_5d_return(klines, CFG_5D) == 35

    def test_max_penalty_above_25pct(self) -> None:
        klines = _klines(100, 105, 110, 118, 125, 130)  # 30%
        assert _penalty_5d_return(klines, CFG_5D) == 35

    def test_insufficient_data(self) -> None:
        assert _penalty_5d_return(_klines(100, 101, 102, 103, 104), CFG_5D) == 0

    def test_custom_thresholds(self) -> None:
        """非默认阈值应改变线性惩罚区间。"""
        cfg = {
            "penalty_5d_return_max": 40,
            "threshold_5d_return_low": 10.0,
            "threshold_5d_return_high": 30.0,
        }
        klines = _klines(100, 104, 108, 112, 116, 120)  # 20%
        # (20-10)/(30-10) * 40 = 20
        assert _penalty_5d_return(klines, cfg) == pytest.approx(20.0)


# ---------- _penalty_ma5_deviation ----------

CFG_MA = {"penalty_ma5_deviation_max": 25}

class TestPenaltyMA5Deviation:
    def test_no_penalty_below_2pct(self) -> None:
        # MA5 = 100, close = 101, dev = 1% < 2%
        klines = _klines(100, 99, 100, 101, 101)
        assert _penalty_ma5_deviation(klines, CFG_MA) == 0

    def test_midpoint(self) -> None:
        # MA5 = 100, close = 105, dev = 5%
        # (5-2)/6 * 25 = 12.5
        klines = _klines(100, 98.75, 98.75, 98.75, 98.75, 105)
        actual = _penalty_ma5_deviation(klines, CFG_MA)
        assert actual == pytest.approx(12.5, abs=0.5)

    def test_max_penalty_at_8pct(self) -> None:
        # MA5 = 100, close = 108, dev = 8%
        klines = _klines(100, 98, 98, 98, 98, 108)
        actual = _penalty_ma5_deviation(klines, CFG_MA)
        assert actual == pytest.approx(25.0, abs=1.0)

    def test_above_8pct_is_max(self) -> None:
        # MA5 = 100, close = 112, dev = 12% > 8%
        klines = _klines(100, 97, 97, 97, 97, 112)
        actual = _penalty_ma5_deviation(klines, CFG_MA)
        assert actual == 25


# ---------- _penalty_upper_shadow ----------

CFG_SHADOW = {"penalty_upper_shadow_max": 15}


class TestPenaltyUpperShadow:
    """冲高回落惩罚：长上影 + 收盘弱 + 近期上涨 + 当天冲高。"""

    def test_triggered_typical_upper_shadow(self) -> None:
        klines = _upper_shadow_klines()
        actual = _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW)
        # upper_shadow_ratio = 0.8125, close_position = 0.1875
        # factor = 1 + (0.5 - 0.1875) * 0.5 = 1.15625
        expected = 0.8125 * 15 * 1.15625
        assert actual == pytest.approx(expected, abs=0.1)

    def test_not_triggered_when_no_upper_shadow(self) -> None:
        klines = _upper_shadow_klines(close=10.8)
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_not_triggered_when_close_in_upper_half(self) -> None:
        klines = _upper_shadow_klines(close=10.5)
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_not_triggered_when_3d_return_low(self) -> None:
        klines = _upper_shadow_klines(close_3d_ago=9.9)
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_not_triggered_when_high_return_from_pre_close_low(self) -> None:
        klines = _upper_shadow_klines(
            pre_close=10.0,
            open_=10.0,
            high=10.03,
            low=10.0,
            close=10.01,
            close_3d_ago=9.2,
        )
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_missing_ohlc_returns_zero(self) -> None:
        klines = _upper_shadow_klines()
        klines[-1]["high"] = None
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_missing_pre_close_returns_zero(self) -> None:
        klines = _upper_shadow_klines()
        klines[-2]["close"] = None
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_zero_amplitude_returns_zero(self) -> None:
        klines = _upper_shadow_klines(open_=10.0, high=10.0, low=10.0, close=10.0)
        assert _penalty_upper_shadow(klines, klines[-1], CFG_SHADOW) == 0

    def test_custom_thresholds(self) -> None:
        cfg = {
            "penalty_upper_shadow_max": 20,
            "threshold_upper_shadow_ratio": 0.7,
            "threshold_close_position": 0.4,
            "threshold_upper_shadow_3d_return": 6.0,
            "threshold_high_return_from_pre_close": 5.0,
        }
        klines = _upper_shadow_klines()
        actual = _penalty_upper_shadow(klines, klines[-1], cfg)
        assert actual > 0

        stricter_cfg = cfg | {"threshold_high_return_from_pre_close": 9.0}
        assert _penalty_upper_shadow(klines, klines[-1], stricter_cfg) == 0


# ---------- _count_consecutive_up ----------


class TestCountConsecutiveUp:
    def test_zero_when_dropping(self) -> None:
        klines = [_kline(10, 11), _kline(9, 10)]  # 连跌 2 天
        assert _count_consecutive_up(klines) == 0

    def test_mixed(self) -> None:
        klines = [
            _kline(10, 9),   # up
            _kline(9, 10),   # down → break
            _kline(11, 10),  # up
            _kline(12, 11),  # up
        ]
        assert _count_consecutive_up(klines) == 2

    def test_all_up(self) -> None:
        klines = [
            _kline(11, 10),
            _kline(12, 11),
            _kline(13, 12),
            _kline(14, 13),
        ]
        assert _count_consecutive_up(klines) == 4


# ---------- _penalty_consecutive_up ----------

CFG_CONSEC = {"penalty_consecutive_up_max": 10}

class TestPenaltyConsecutiveUp:
    def test_3_4_days_low(self) -> None:
        klines = [_kline(10 + i, 9 + i) for i in range(4)]
        assert _penalty_consecutive_up(klines, CFG_CONSEC) == pytest.approx(10 * 0.35)

    def test_5_6_days_medium(self) -> None:
        klines = [_kline(10 + i, 9 + i) for i in range(6)]
        assert _penalty_consecutive_up(klines, CFG_CONSEC) == pytest.approx(10 * 0.7)

    def test_7_plus_days_max(self) -> None:
        klines = [_kline(10 + i, 9 + i) for i in range(8)]
        assert _penalty_consecutive_up(klines, CFG_CONSEC) == 10

    def test_less_than_3_returns_0(self) -> None:
        klines = [_kline(11, 10), _kline(12, 11)]
        assert _penalty_consecutive_up(klines, CFG_CONSEC) == 0

    def test_custom_thresholds_and_ratios(self) -> None:
        """连涨分档阈值和惩罚比例应从 cfg 读取。"""
        cfg = {
            "penalty_consecutive_up_max": 20,
            "threshold_consecutive_up_low": 2,
            "threshold_consecutive_up_mid": 4,
            "threshold_consecutive_up_high": 6,
            "ratio_consecutive_up_low": 0.25,
            "ratio_consecutive_up_mid": 0.6,
        }

        two_days = [_kline(10 + i, 9 + i) for i in range(2)]
        four_days = [_kline(10 + i, 9 + i) for i in range(4)]
        six_days = [_kline(10 + i, 9 + i) for i in range(6)]

        assert _penalty_consecutive_up(two_days, cfg) == pytest.approx(5.0)
        assert _penalty_consecutive_up(four_days, cfg) == pytest.approx(12.0)
        assert _penalty_consecutive_up(six_days, cfg) == pytest.approx(20.0)


# ---------- _penalty_momentum_decay ----------

CFG_DECAY = {"penalty_momentum_decay_max": 10}

class TestPenaltyMomentumDecay:
    def test_no_penalty_when_5d_return_low(self) -> None:
        # 5d return = 5% < 10% threshold
        klines = _klines(100, 101, 102, 103, 104, 105)
        assert _penalty_momentum_decay(klines, CFG_DECAY) == 0

    def test_no_penalty_when_momentum_fresh(self) -> None:
        # 5d +20%, 2d +10% → 2d is 50% of 5d > 20%
        klines = _klines(100, 104, 108, 110, 115, 120)
        assert _penalty_momentum_decay(klines, CFG_DECAY) == 0

    def test_penalty_when_momentum_stale(self) -> None:
        # 5d +25%, 2d +2% → 2d is 8% of 5d < 20%
        klines = _klines(100, 108, 115, 122, 123, 125)
        # 5d: 100→125 = 25%
        # 2d: 122→125 ≈ 2.5%
        # 2.5% < 25% * 0.2 = 5%
        actual = _penalty_momentum_decay(klines, CFG_DECAY)
        assert actual == 10

    def test_insufficient_data(self) -> None:
        klines = _klines(100, 101, 102, 103, 104)
        assert _penalty_momentum_decay(klines, CFG_DECAY) == 0

    def test_5d_positive_but_2d_negative_stale(self) -> None:
        # 5d +15%, 2d -3% → momentum clearly decayed
        klines = _klines(100, 106, 112, 118, 116, 115)
        # 5d: 100→115 = +15%
        # 2d: 118→115 ≈ -2.5%
        # -2.5% < 15% * 0.2 = 3% → penalty
        assert _penalty_momentum_decay(klines, CFG_DECAY) == 10


# ---------- _penalty_volume_divergence ----------

CFG_VOL = {"penalty_volume_divergence_max": 20}


class TestPenaltyVolumeDivergence:
    """量价背离惩罚：缩量上涨 + 放量滞涨两种模式。"""

    # --- 缩量上涨 ---

    def test_shrink_up_triggered(self) -> None:
        """近 3 日涨 10%，今日量仅 5 日均量的 25% → 严重缩量上涨。"""
        # klines[-4] = index 2 close=97, today close=110 → 3d return > 5%
        klines = [
            _kline(90, vol=2000),    # [0]
            _kline(94, vol=2000),    # [1]
            _kline(97, vol=2000),    # [2] klines[-4]
            _kline(100, vol=2000),   # [3]
            _kline(108, vol=2000),   # [4]
            _kline(110, vol=500),    # [5] today
        ]
        today = klines[-1]
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        # ratio = 500/2000 = 0.25, severity = (0.6-0.25)/0.6 = 0.583
        # penalty = 0.583 * 20 ≈ 11.67
        assert penalty > 5
        assert penalty < 20

    def test_shrink_up_not_triggered_volume_ok(self) -> None:
        """近 3 日涨 8%，但量没缩到 60% → 不触发。"""
        closes = [100, 101, 102, 105, 108, 110]
        klines = [_kline(c, vol=2000) for c in closes]
        # today_vol=2000, avg_vol=2000, ratio=1.0 > 0.6
        penalty = _penalty_volume_divergence(klines, klines[-1], CFG_VOL)
        assert penalty == 0

    def test_shrink_up_not_triggered_return_low(self) -> None:
        """近 3 日涨幅不足 5%，即使缩量也不触发。"""
        # 3d return: (102-100)/100*100 = 2%
        closes = [98, 99, 100, 100, 101, 102]
        klines = [
            _kline(c, vol=2000 if i < 5 else 400) for i, c in enumerate(closes)
        ]
        penalty = _penalty_volume_divergence(klines, klines[-1], CFG_VOL)
        assert penalty == 0

    # --- 放量滞涨 ---

    def test_blowoff_triggered(self) -> None:
        """今日量是均量 3 倍，但相对昨收几乎没涨 → 放量滞涨。"""
        # 5 日均量 1000，今日量 3000，昨收 10.0，今收 10.1（+1%）
        closes = [9.0, 9.3, 9.6, 9.8, 10.0, 10.1]
        klines = [
            _kline(c, vol=1000) for c in closes
        ]
        klines[-1] = _kline(10.1, vol=3000)
        today = klines[-1]
        # pct_chg = (10.1-10.0)/10.0*100 = 1% < 2%
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        # max_penalty * 0.8 = 16
        assert penalty == pytest.approx(16.0, abs=0.1)

    def test_blowoff_triggered_negative_pct_chg(self) -> None:
        """放量微跌也触发——'滞涨'包含微跌。"""
        closes = [9.0, 9.3, 9.6, 9.8, 10.0, 9.99]
        klines = [
            _kline(c, vol=1000) for c in closes
        ]
        klines[-1] = _kline(9.99, vol=3000)
        today = klines[-1]
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        # pct_chg = (9.99-10.0)/10.0*100 = -0.1% < 2%
        assert penalty == pytest.approx(16.0, abs=0.1)

    def test_blowoff_not_triggered_normal_up(self) -> None:
        """放量但涨幅达标 → 不触发（这是健康的放量上涨）。"""
        closes = [9.0, 9.3, 9.6, 9.8, 10.0, 10.5]
        klines = [
            _kline(c, vol=1000) for c in closes
        ]
        klines[-1] = _kline(10.5, vol=3000)
        today = klines[-1]
        # pct_chg = (10.5-10.0)/10.0*100 = 5% > 2%
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        assert penalty == 0

    def test_blowoff_not_triggered_volume_normal(self) -> None:
        """价格停滞但量没放大 → 不是放量滞涨。"""
        closes = [9.0, 9.3, 9.6, 9.8, 10.0, 10.05]
        klines = [_kline(c, vol=1000) for c in closes]
        penalty = _penalty_volume_divergence(klines, klines[-1], CFG_VOL)
        assert penalty == 0

    # --- 边界情况 ---

    def test_insufficient_data(self) -> None:
        """K 线不足 6 条（计算均量需要不含今日的 5 天历史量）。"""
        closes = [100, 102, 105, 108, 110]
        klines = [_kline(c, vol=1000) for c in closes]
        penalty = _penalty_volume_divergence(klines, klines[-1], CFG_VOL)
        assert penalty == 0

    def test_both_modes_shrink_wins(self) -> None:
        """两种模式同时触发时取 max——此时缩量上涨惩罚可能更大。"""
        klines = [
            _kline(100, vol=2000),
            _kline(100, vol=2000),
            _kline(100, vol=2000),
            _kline(110, vol=2000),
            _kline(115, vol=2000),
            _kline(120, vol=300),  # 量极缩 + 涨幅大
        ]
        today = klines[-1]
        # shrink: 3d (100→120) = 20% > 5%, vol ratio 300/2000=0.15 < 0.6
        # severity = (0.6-0.15)/0.6 = 0.75, penalty = 0.75*20 = 15
        # blowoff: vol 300 < 2000*2, not triggered
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        assert penalty == pytest.approx(15.0, abs=0.5)

    def test_no_volume_data_graceful(self) -> None:
        """vol 字段缺失时优雅返回 0。"""
        klines = [_kline(100), _kline(101), _kline(102), _kline(103), _kline(104)]
        klines.append(_kline(105))  # all without vol
        today = klines[-1]
        penalty = _penalty_volume_divergence(klines, today, CFG_VOL)
        assert penalty == 0
