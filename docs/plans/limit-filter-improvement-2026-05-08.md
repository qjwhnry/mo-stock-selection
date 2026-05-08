# 短线断板反包逻辑改进计划

> 触发案例：2026-05-07 汉钟精机（002158.SZ）连板后断板入选
> 日期：2026-05-08

---

## 1. 案例回溯

| 日期 | 事件 | 封单 | 首次封板 | 换手率 |
|------|------|------|----------|--------|
| 4/30 | 首板涨停 +10% | 1.06 亿 | 13:32（偏晚） | 2.89% |
| 5/6 | 二连板 +10% | 0.54 亿（-49%） | 10:57 | 5.41% |
| 5/7 | 断板 +5.27%，近涨停乏力回落 | — | — | 6.75% |

5/7 高开 30.68 → 最高 33.33（距涨停 33.41 差 0.08 元，未真正触板）→ 回落收 31.97。振幅 10.5%，上影线极长。

**当日评分**：

| 维度 | 得分 | 关键点 |
|------|------|--------|
| limit | **70** | 断板反包，涨 5%+ 档位 |
| moneyflow | 40 | `net_mf` 流入 + 大单流出被 -30 惩罚后 |
| lhb | 75 | 被动触发上榜 + 机构/北向净买 |
| theme | 55 | 概念涨停排名 2 |
| **rule** | **48.0** | → AI 62 → **final 53.60，全市场 #2** |

**核心矛盾**：大单净流出 7221 万 + 小单净流入 9309 万的出货结构，limit 维度仍给 70 分。

---

## 2. 根因分析

### 问题 1：断板反包不区分连板位置

`_break_board_rebound_bonus`（`limit_filter.py:218`）仅根据当日涨幅分档，不区分：

- 首板后断板 → 正常分歧，可能蓄势
- 二连板后断板 → 多头力竭，高位分歧
- 三连板以上断板 → 极端行情

当前 3 种情况都是同样的 5~8% → 70 分。

### 问题 2：大单流出惩罚与连板上下文脱节

`small_up_big_down_penalty = -30` 是全局固定值。在连板后断板 + 大单净流出 + 小单推动的三重叠
加场景下，-30 兜不住 50+20 的正向分，导致 moneyflow 仍有 40 分。

### 问题 3：缺乏盘中走势质量维度

断板日两种走势隐含信息完全不同：

| 走势 | 信号 | 当前分数 |
|------|------|----------|
| 低开→稳步上行→收 5% | 健康承接 | 70 |
| 高开→冲高近板乏力→回落收 5% | 出货迹象重 | **70（相同）** |

后者当日 `high >= pre_close * 1.09`（距涨停 1% 内）但未触板，且 `close < open`（阴线），
应额外扣分。

### 问题 4（GLM 补充）：封单衰减趋势未利用

4/30 封单 1.06 亿 → 5/6 封单 0.54 亿（降 49%），封板意愿递减信号明确但未被使用。

---

## 3. 改进方案

### 改进 A：连板序列感知（高优先级）

**文件**：`src/mo_stock/filters/short/limit_filter.py`

**改动点**：`_break_board_rebound_bonus` 增加 `board_position` 参数。

```python
def _break_board_rebound_bonus(
    yesterday_was_limit_up: bool,
    today_is_limit_up: bool,
    today_pct_chg: float | None,
    board_position: int = 1,          # 新增：前一日连板位置
) -> int:
    """断板反包加分。board_position 越大，断板信号越偏空。

    board_position = 1: 首板后断板，不变
    board_position = 2: 二连板后断板，各档 -20
    board_position >= 3: 三连板以上，封顶 40
    """
    if not yesterday_was_limit_up or today_is_limit_up:
        return 0
    if today_pct_chg is None or today_pct_chg < 1.0:
        return 0

    if board_position <= 1:
        # 首板后断板：现有逻辑不变
        bonus_table = {8.0: 100, 5.0: 70, 3.0: 50, 1.0: 30}
    elif board_position == 2:
        # 二连板后断板：各档 -20
        bonus_table = {8.0: 80, 5.0: 50, 3.0: 30, 1.0: 15}
    else:
        # 三连板以上：上限 40
        bonus_table = {8.0: 40, 5.0: 30, 3.0: 20, 1.0: 10}

    for threshold, bonus in bonus_table.items():
        if today_pct_chg >= threshold:
            return bonus
    return 0
```

**调用侧改动**（`score_all` 中断板反包循环）：

```python
# 获取前一日连板数（从 limit_context 共享模块 import）
from mo_stock.filters.short.limit_context import get_prev_limit_times_map
prev_limit_times_map = get_prev_limit_times_map(session, prev_trade_date)

# 循环内：
board_position = prev_limit_times_map.get(ts_code, 1)
rebound = _break_board_rebound_bonus(
    yesterday_was_limit_up, today_is_limit_up=False,
    today_pct_chg=today_pct, board_position=board_position,
)
```

**共享辅助函数**（定义在 `src/mo_stock/filters/short/limit_context.py`，A 和 B 都 import）：

```python
# src/mo_stock/filters/short/limit_context.py

def parse_limit_times(limit_times: int | None, up_stat: str | None) -> int:
    """从 limit_times 或 up_stat 解析连板数。

    Tushare 某些日期 limit_times 可能为空但 up_stat 有值（如 "2/3"）。
    与原 LimitFilter._parse_limit_times 逻辑一致，抽取为独立函数避免跨 filter 依赖。
    """
    if limit_times:
        return limit_times
    if up_stat and "/" in up_stat:
        try:
            return int(up_stat.split("/")[0])
        except (ValueError, IndexError):
            pass
    return 1


def get_prev_limit_times_map(
    session: Session, trade_date: date,
) -> dict[str, int]:
    """批量获取指定交易日涨停股的连板数。"""
    from sqlalchemy import select
    from mo_stock.storage.models import LimitList
    rows = session.execute(
        select(LimitList.ts_code, LimitList.limit_times, LimitList.up_stat)
        .where(LimitList.trade_date == trade_date)
        .where(LimitList.limit_type == "U")
    ).all()
    return {r[0]: parse_limit_times(r[1], r[2]) for r in rows}
```

### 改进 B：连板后出货信号加重惩罚（高优先级）

**文件**：`src/mo_stock/filters/short/moneyflow_filter.py`

**改动点**：`small_up_big_down_penalty` 分级，连板后断板场景加重。

**注意**：此改动让 `moneyflow_filter` 新增了对 `limit_list` 表的依赖（原本只查 `moneyflow` +
`daily_kline`），在代码中需注释说明这个跨 filter 数据依赖。

**共有依赖**：改进 B 和 A 共用 `get_prev_limit_times_map`（定义在 `limit_context.py`，见改进 A），避免重复查询和循环依赖。

在 `MoneyflowFilter.score_all` 中，先查当日涨停集合 + 前日连板集：

```python
from mo_stock.filters.short.limit_context import get_prev_limit_times_map

# 当日涨停股集合（排除"昨日二板、今日继续三板"的连板延续股）
today_limit_codes = repo.get_limit_up_codes(session, trade_date)

# 前日连板≥2 的涨停股集合（需用交易日历获取前一交易日，不能用自然日 -1）
prev_trade_date = previous_trading_day(session, trade_date)
if prev_trade_date is None:
    multi_board_rebound_codes = set()
else:
    prev_limit_times_map = get_prev_limit_times_map(session, prev_trade_date)
    multi_board_rebound_codes = {
        ts for ts, times in prev_limit_times_map.items() if times >= 2
    }
```

**完整扣分逻辑**（替换现有 lines 133-139 的固定 `-30`）：

```python
# 原逻辑：小单净流入但大单净流出 → 固定惩罚
buy_sm = row.buy_sm_amount or 0
sell_sm = row.sell_sm_amount or 0
small_net = buy_sm - sell_sm
if small_net > 0 and big_net < 0:
    penalty = small_up_big_down_penalty              # 默认 30
    if (ts_code in multi_board_rebound_codes
            and ts_code not in today_limit_codes):   # 二板+断板，加倍
        penalty *= 2                                 # 30 → 60
        detail["doubled_penalty_reason"] = "二连板+断板出货信号叠加"
    score -= penalty
    detail["small_up_big_down_penalty"] = -penalty
```

**为什么 `ts_code not in today_limit_codes` 关键**：不加这个条件，"昨日二板、今日继续三板"的
股票也会被打成"断板出货"，虽然 `hard_reject.exclude_today_limit_up` 最终会过滤掉，但
`filter_score_daily` 里会残留错误分数，analyzer 和调试都会受污染。

这样汉钟精机的 moneyflow 就会变成：50 + 0 + 20 - **60** = 10（大幅压低）。

### 改进 C：冲高回落全天位置检测 + 动态涨停阈值（中优先级）

**文件**：`src/mo_stock/filters/short/limit_filter.py`

**前置改动**：当前 `kline_pct_map` 只查 `pct_chg`（line 57），C 需要完整 OHLCV。
`pre_close` 在 DailyKline 模型中存在（line 115），`low` 用于计算全天位置。

```python
# 改前：只查 pct_chg
kline_pct_rows = session.execute(
    select(DailyKline.ts_code, DailyKline.pct_chg)
    .where(DailyKline.trade_date == trade_date),
).all()

# 改后：查完整 OHLCV
kline_rows = session.execute(
    select(DailyKline.ts_code, DailyKline.pct_chg,
           DailyKline.open, DailyKline.high, DailyKline.low,
           DailyKline.close, DailyKline.pre_close)
    .where(DailyKline.trade_date == trade_date),
).all()
# 构建 ohlc_map: {ts_code: {open, high, low, close, pre_close, pct_chg}}
# kline_pct_map 从 ohlc_map 派生，保持旧引用兼容
```

**改动点**：

**3.1 动态涨停阈值**——不做已知不准的让步：

```python
def _limit_up_ratio(ts_code: str) -> float:
    """按股票代码/板块返回涨停幅度。

    主板（60xxxx / 00xxxx）：1.10
    创业板（30xxxx）、科创板（688xxx）：1.20
    ST 不在本函数处理——已被 hard_reject（exclude_st）过滤，不会进入打分流程。
    """
    if ts_code.startswith(("300", "301", "688")):
        return 1.20
    return 1.10


def _near_limit_up_threshold(pre_close: float, limit_up_ratio: float) -> float:
    """近涨停阈值 = 涨停价 * 0.99（距涨停 1% 以内）。
    例：10cm 涨停价 pre*1.10 → 阈值 pre*1.10*0.99 = pre*1.089。
    """
    limit_price = pre_close * limit_up_ratio
    return limit_price * 0.99
```

**3.2 全天收盘位置判断**——替代 `上影线 > 实体`，不漏十字星冲高回落：

```python
def _near_limit_up_fade_bonus(
    ts_code: str,
    today_high: float | None,
    today_low: float | None,
    today_close: float | None,
    pre_close: float | None,
) -> int:
    """当日近涨停未封 + 收盘在全天振幅下半区 → 冲高回落负分。

    条件：
    1. high >= 动态近涨停阈值（距该股涨停价 1% 以内）
    2. 收盘在全天振幅的下半区（close_position <= 0.5）

    不依赖 open 字段——算法只用 high/low/close/pre_close，
    十字星（open=close）不影响判定。

    返回 0 或 -20。
    """
    if None in (today_high, today_low, today_close, pre_close):
        return 0

    near_limit_threshold = _near_limit_up_threshold(pre_close, _limit_up_ratio(ts_code))
    near_limit = today_high >= near_limit_threshold

    day_range = today_high - today_low
    if day_range <= 0:
        return 0  # 一字板 / 无波动，不惩罚

    close_position = (today_close - today_low) / day_range
    fade = close_position <= 0.5  # 收盘在全天振幅下半区

    return -20 if (near_limit and fade) else 0
```

汉钟精机验证（002158.SZ，主板 10cm）：
- 涨停价 = 30.37 * 1.10 = 33.41，近板阈值 = 33.41 * 0.99 = 33.07
- high=33.33 >= 33.07 ✓
- day_range = 33.33 - 30.15 = 3.18
- close_position = (31.97 - 30.15) / 3.18 = 0.572
- 0.572 <= 0.5? **否** → 不触发

汉钟精机不会触发这个条件。但这不是函数的问题——数据本身说明：
收盘在振幅中位偏上（57.2%），卖盘并未把价格打到全天下半区。这不是一只"被砸回来"的股，
而是一只在上涨中遭遇卖压但收盘仍站稳的股。**C 的正确定义不应强制惩罚它**。

真正该触发 C 的典型：开盘 30 → 最高 33（近板）→ 回落收 30.5，low=29.5。
close_position = (30.5-29.5)/3.5 = 0.29。这才叫冲高回落。

叠加 A：limit 70 → 50（A）= **最终 50**。B 独立作用将 moneyflow 压到 10。

### 改进 D：封单衰减系数（低优先级）

**文件**：`src/mo_stock/filters/short/limit_filter.py`

**改动点**：`_break_board_rebound_bonus` 调用前批量查询封单衰减，非逐股查询。

```python
def _get_seal_decay_map(
    session: Session,
    prev_trade_date: date,
    prev_prev_trade_date: date,
) -> dict[str, float]:
    """批量计算封单衰减比 → {ts_code: seal_ratio}。

    只对连续两天都有涨停且前日封单下降 > 30% 的股返回 < 0.7 的值。
    循环内只需 dict lookup，避免 N+1 查询。
    """
    rows = session.execute(
        select(
            LimitList.ts_code,
            LimitList.trade_date,
            LimitList.fd_amount,
        )
        .where(LimitList.trade_date.in_([prev_trade_date, prev_prev_trade_date]))
        .where(LimitList.limit_type == "U")
        .order_by(LimitList.trade_date.asc())
    ).all()

    by_stock: dict[str, list[float]] = {}
    for ts_code, _, fd_amount in rows:
        by_stock.setdefault(ts_code, []).append(fd_amount or 0)

    result: dict[str, float] = {}
    for ts_code, amounts in by_stock.items():
        if len(amounts) < 2:
            continue
        older, newer = amounts[0], amounts[1]
        if older > 0:
            result[ts_code] = newer / older
    return result
```

调用侧：
```python
seal_decay_map = _get_seal_decay_map(session, prev_trade_date, prev_prev_trade_date)
# 循环内：
seal_ratio = seal_decay_map.get(ts_code, 1.0)
seal_penalty = -10 if seal_ratio < 0.7 else 0
```

注意：跨节假日时，`prev_prev_trade_date` 需用交易日历（`previous_trading_day` 跳两次）。

---

## 4. 实施优先级 & 预期效果

| 改进 | 优先级 | 工作量 | 汉钟精机效果 |
|------|--------|--------|-------------|
| A 连板序列感知 | **高** | ~30 行 | limit 70 → 50 |
| B 出货信号加重 | **高** | ~15 行（含共享函数） | moneyflow 40 → 10 |
| C 动态阈值+全天位置 | **中** | ~25 行 | 不触发（close_position=0.57 > 0.5） |
| D 封单衰减系数 | **低** | ~20 行 | limit 再 -10 |

**综合预期**：

| 场景 | limit | moneyflow | lhb | theme | rule_score |
|------|-------|-----------|-----|-------|------------|
| 当前 | 70 | 40 | 75 | 55 | **48.0** |
| A 单独 | 50 | 40 | 75 | 55 | **43.0** |
| A + B | 50 | 10 | 75 | 55 | **35.5** |
| A + B + C | 50 | 10 | 75 | 55 | **35.5**（同 A+B） |

公式：A+B: 50*0.25 + 10*0.25 + 75*0.20 + 55*0.10 = 12.5 + 2.5 + 15.0 + 5.5 = 35.5。
对应当前 Top 20 入选线约 46-47，A+B 后已排除。A 单独（43.0）边界，不够稳妥。

**实施顺序**：
1. **A + C**（一个 PR，都在 `LimitFilter` 内，逻辑闭环风险低）
2. **B**（单独 PR，跨 filter + 可配置，依赖 A 的共享函数）
3. **D**（回测后再决定）

---

## 5. 测试用例

```python
# tests/unit/filters/short/test_limit_filter.py（沿用现有结构，新增测试用例）

class TestBreakBoardRebound:
    def test_first_board_rebound_unchanged(self):
        """首板后断板涨 5% 仍得 70，兼容旧行为。"""
        assert _break_board_rebound_bonus(
            True, False, 5.27, board_position=1
        ) == 70

    def test_second_board_rebound_penalized(self):
        """二连板后断板涨 5% → 50（原 70）。"""
        assert _break_board_rebound_bonus(
            True, False, 5.27, board_position=2
        ) == 50

    def test_third_board_rebound_capped(self):
        """三连板以上断板涨 5% → 30（原 70）。"""
        assert _break_board_rebound_bonus(
            True, False, 5.27, board_position=3
        ) == 30

    def test_non_rebound_zero(self):
        """昨未涨停，不触发断板反包。"""
        assert _break_board_rebound_bonus(
            False, False, 5.0, board_position=1
        ) == 0

    def test_today_limit_up_not_rebound(self):
        """今日继续涨停 = 连板非断板。"""
        assert _break_board_rebound_bonus(
            True, True, 10.0, board_position=1
        ) == 0


class TestDynamicLimitUpRatio:
    def test_main_board_10cm(self):
        assert _limit_up_ratio("002158.SZ") == 1.10
        assert _limit_up_ratio("600519.SH") == 1.10

    def test_gem_star_20cm(self):
        assert _limit_up_ratio("300750.SZ") == 1.20
        assert _limit_up_ratio("301001.SZ") == 1.20

    def test_star_market_20cm(self):
        assert _limit_up_ratio("688981.SH") == 1.20


class TestNearLimitUpFade:
    """全天收盘位置判断（close_position = (close - low) / (high - low)）。"""

    def test_true_fade_close_in_lower_half(self):
        """近涨停 + 收盘在下半区（close_position=0.29）→ -20。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=29.5, today_close=30.5,
            pre_close=30.0,
        ) == -20

    def test_hanzhong_no_trigger(self):
        """汉钟精机 5/7 真实数据：close_position=0.572 > 0.5 → 不触发。
        数据不支持"冲高回落"判定——收盘在振幅中位偏上，卖压未控盘。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.33, today_low=30.15,
            today_close=31.97, pre_close=30.37,
        ) == 0

    def test_strong_uptrend_no_penalty(self):
        """近涨停 + 收盘在上半区（close_position=0.86）→ 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="600519.SH",
            today_high=33.0, today_low=29.8, today_close=32.5,
            pre_close=29.5,
        ) == 0

    def test_doji_fade_triggered(self):
        """十字星冲高回落（body≈0，close_position=0.2）→ 仍然触发。
        冲到近板后收回开盘附近是强分歧信号，不应因 body=0 跳过。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=29.0, today_close=29.8,
            pre_close=30.0,
        ) == -20

    def test_far_from_limit_no_penalty(self):
        """距涨停很远 → 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=31.5, today_low=29.0, today_close=29.3,
            pre_close=29.5,
        ) == 0

    def test_20cm_stock_near_limit(self):
        """20cm 股票用 1.20 阈值，不是 1.09。"""
        # 创业板，涨停价 = pre*1.20=36.0，近板阈值 = 36.0*0.99=35.64
        assert _near_limit_up_fade_bonus(
            ts_code="300750.SZ",
            today_high=35.8, today_low=29.0, today_close=30.2,
            pre_close=30.0,
        ) == -20  # 35.8 >= 35.64 ✓, close_position=0.17 ✓

    def test_one_line_limit_no_penalty(self):
        """一字板 day_range=0 → 0。"""
        assert _near_limit_up_fade_bonus(
            ts_code="002158.SZ",
            today_high=33.0, today_low=33.0, today_close=33.0,
            pre_close=30.0,
        ) == 0
```

---

## 6. 注意事项

1. **交易日历**：改进 A 的连板查询、改进 D 的封单衰减对比，都必须使用 `previous_trading_day` 而非自然日 -1（避免跨周末/长假错误）
2. **性能**：`get_prev_limit_times_map` 和 `_get_seal_decay_map` 一次查询全量，不应在循环内逐股查
3. **跨 filter 共享函数**：`get_prev_limit_times_map` 和 `parse_limit_times` 定义在 `src/mo_stock/filters/short/limit_context.py`，A（LimitFilter）和 B（MoneyflowFilter）都 import 它，避免 MoneyflowFilter 间接依赖 LimitFilter
4. **权重文件可配置化**：B 的加倍惩罚是否启用可通过 `weights.yaml` 的 `moneyflow_filter.double_penalty_for_multi_board_rebound: true/false` 控制，方便回测时开关
5. **回测**：修改后应重新跑 `short_backtest` 看胜率/IR 变化，确保改进不破坏整体选股质量
6. **不是所有断板都差**：首板后正常分歧断板（如低开走高收阳线 + 大单净流入）仍然是有效信号，改进 A/B 只惩罚连板后断板场景

---

## 7. 审核修订记录

### Round 3（2026-05-08）：Codex 最终审核 —— "不计成本"标准

1. **B 增加 `today_limit_codes` 排除**：加入 `ts_code not in today_limit_codes`，防止"昨日二板今日继续三板"的股票在 moneyflow 被错误标记为"断板出货"
2. **B 条件窄化**：完整四条件 `multi_board_rebound + not in today_limit_codes + small_net > 0 + big_net < 0`，而非仅 `in multi_board_rebound_codes`
3. **C 上影线算法重构**：`upper_shadow > body` → `close_position <= 0.5`（收盘在全天振幅下半区），不漏十字星冲高回落，不限阴阳线
4. **C 动态涨停阈值**：`pre_close * 1.09` → `_limit_up_ratio(ts_code)` + `_near_limit_up_threshold`，按主板/创业板/科创板区分 10cm/20cm
5. **C 汉钟精机实际不触发**：close_position=0.572 > 0.5，数据不支持"冲高回落"判定。C 的定义被修正为只惩罚真正被卖盘打回下半区的股票
6. **预期分重算**：A+B 后 rule_score=35.5（A 单独 43.0 不够），C 不改变汉钟精机的分数
7. **共享函数抽取**：`_get_prev_limit_times_map` → `src/mo_stock/filters/short/limit_context.py`
8. **测试用例全覆盖**：新增 `TestDynamicLimitUpRatio`、十字星触发、20cm股票、一字板

### Round 2（2026-05-08）：Codex 审核

1. **B 判定条件过宽**：`ts_code in prev_limit_codes` → `multi_board_rebound_codes`（仅 `limit_times >= 2`）
2. **`limit_times` fallback**：同时 select `up_stat` 字段
3. **K 线字段扩充**：`kline_pct_map` → 完整 `ohlc_map`
4. **9% 阈值限制**：初版按主板 10cm 处理（Round 3 已动态化）
5. **预期分修正**：48 → 30.5（Round 3 进一步修正为 35.5）
6. **实施顺序**：A+C → B → D
7. **测试文件路径**：`tests/unit/filters/short/test_limit_filter.py`

### Round 1（2026-05-08）：GLM 审核

- 改进 C：`close < open` → `上影线 > 实体`（Round 3 进一步改为全天位置）
- 改进 D：逐股查询 → batch `_get_seal_decay_map`
- 改进 B：补充跨 filter 依赖注释
