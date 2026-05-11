# 短线策略实证校准实施计划（v2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v2 修订（2026-05-10）：基于 codex review 修正了 7 项问题**：收益字段统一用 `net_realized_return_pct`（扣费后口径）、`_simulate_holding_period` API 对齐、`get_session` import 路径修正、明确 picked 选股偏差并新增 `rank_in_day` 分组缓解、`active_dims` / 维度组合分析剥离 `exhaustion`、补充 `sector_l1` 分组任务、文件名内置 `holding_days` 替换 `--output-suffix`。

**Goal:** 用历史回测的**扣费后净收益**数据校准短线策略——回答"综合分排序是否有效""多维共振假设是否成立""哪些催化组合最有效""1/3 日哪个持有期更优""哪些行业是强项/弱项"。

**Architecture:** 三阶段。

阶段 1 把 `short_backtest` 默认成本从 0 改为 A 股实际值（印花税 + 佣金 + 滑点），让 `net_realized_return_pct` 反映真实净收益。

阶段 2 在 `src/mo_stock/backtest/calibration.py` 新建分析模块。所有函数默认统计 `net_realized_return_pct`，可通过 `return_field` 参数切换。包括：rule_score 分档、催化维度数分组（**只算 limit/moneyflow/lhb/sector/theme，不含 exhaustion**）、`rank_in_day` 分组（缓解"只存 picked Top N"的样本选择偏差）、sector_l1 分组、维度组合分析（默认排除 exhaustion）、exhaustion 质量分档（独立做）。

阶段 3 跑 6 个月历史回测，产出 1d/3d 两份 markdown 报告，写决策文档。

**已知方法论限制（必须在报告中标注）**：当前 `short_backtest_trade` 表只持久化每日 picked Top N，不含全部 scored candidates。rule_score 分档分析存在选股偏差——低分能进 picked 通常发生在市场弱日（候选少导致门槛降低），与"低分股票本身"的真实表现不可分离。本 plan 通过新增 `rank_in_day` 分组（同一日内排名段对比）部分缓解，根本解决方案是后续 plan 扩展 short_engine 落库全部 scored candidates。

**Tech Stack:** PostgreSQL + SQLAlchemy（ORM 读 `short_backtest_trade`）、Python（statistics / dataclasses）、pytest（TDD）、Markdown（报告）。

---

## File Structure

| 文件 | 类型 | 责任 |
|---|---|---|
| `config/weights.yaml` | 修改 | `short_backtest` 节的成本默认值改为 A 股实际值 |
| `src/mo_stock/backtest/calibration.py` | 新建 | 校准分析的纯函数（rule_score / catalyst_dims / rank_in_day / sector / 组合 / exhaustion）+ markdown 渲染 |
| `scripts/calibrate_short_strategy.py` | 新建 | 薄入口：读最新 run_id → 调 calibration → 写 docs/ |
| `tests/unit/backtest/test_short_calibration.py` | 新建 | calibration 模块所有纯函数的单元测试（用 SimpleNamespace 模拟 trade 行） |
| `tests/unit/backtest/test_short_backtest.py` | 修改 | 新增 1 个测试：成本扣费正确应用到 net_realized_return_pct |
| `docs/short-strategy-calibration-2026-05-10-1d.md` | 新建（plan 产出） | 1 日持有期校准报告 |
| `docs/short-strategy-calibration-2026-05-10-3d.md` | 新建（plan 产出） | 3 日持有期校准报告 |
| `docs/short-strategy-calibration-decisions-2026-05-10.md` | 新建（plan 产出） | 基于上面两份报告的优化决策记录 |

**关键约定**：
- calibration 模块所有函数的默认收益字段是 `net_realized_return_pct`（扣费后），可通过 `return_field` 参数切换
- 催化维度（catalyst dims）= `{limit, moneyflow, lhb, sector, theme}`，**不含 exhaustion**（exhaustion 是动量质量维度，不是正向催化）
- 函数命名前缀：`xxx_groups` 返回 `dict[str, BucketStats]`，`xxx_buckets` 返回 `list[BucketStats]`，`xxx_analysis` 返回排序后的 `list[BucketStats]`

---

## Task 1: 修正 short_backtest 成本默认值

**Files:**
- Modify: `config/weights.yaml:202-211`

A 股 2026 年实际成本（保守估计）：买入滑点 0.20% + 卖出滑点 0.20% + 佣金 0.025%（**单边费率，买卖两边各扣一次**） + 卖方印花税 0.05%。单笔往返总成本 ≈ 0.5%。

- [ ] **Step 1: 修改 yaml**

把 `config/weights.yaml` 第 202-211 行（`short_backtest:` 节）改为：

```yaml
short_backtest:
  stop_loss_pct: 5.0
  holding_days: [1, 2, 3, 5]
  skip_limit_up_gap: true
  buy_slippage_pct: 0.20    # 买入滑点（A 股 1-3 日短线常见值）
  sell_slippage_pct: 0.20   # 卖出滑点
  commission_pct: 0.025     # 佣金万分之 2.5（_net_return 在买卖两端各扣一次）
  tax_pct: 0.05             # 卖出印花税千分之零点五（2023-08-28 后口径）
```

- [ ] **Step 2: 跑现有回测单测确保未回归**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_backtest.py -v`
Expected: 全部 PASS（现有测试都显式传 `cost_cfg={}` 覆盖默认值，不会受 yaml 改动影响）

- [ ] **Step 3: Commit**

```bash
git add config/weights.yaml
git commit -m "chore: 短线回测默认成本设为 A 股实际值

- buy/sell_slippage 0.20%（短线滑点）
- commission 0.025%（单边万 2.5，_net_return 在买卖两端各扣一次）
- tax 0.05%（卖出印花税，2023-08 后口径）
- 单笔往返总成本 ~0.5%，让 net_realized_return_pct 反映真实净收益"
```

---

## Task 2: 增加成本扣费集成测试

**Files:**
- Modify: `tests/unit/backtest/test_short_backtest.py`

确认 `_simulate_holding_period` 实际签名（keyword-only，返回 dict）下扣费正确联动。

- [ ] **Step 1: 在 test 文件末尾追加测试**

打开 `tests/unit/backtest/test_short_backtest.py`，确认顶部已 import `pytest`（如缺失补上）。`date` 等其它依赖在测试函数内部 import，无需顶部声明。在文件末尾追加：

```python
def test_simulate_holding_period_applies_costs_to_net_return(sqlite_session) -> None:
    """成本扣费验证：买入 100，持有 2 日（entry 日 + 次日）次日收 105，扣完成本 net_realized 应约 +4.5%。

    注意：future_trade_dates(start=entry_date, days=N) 是 cal_date >= start + limit(N)，
    所以 holding_days=1 时 planned_exit == entry_date（当天退出），用不到次日 K 线。
    要让退出价为次日 close，holding_days 必须传 2。

    买入参考价 100，第 2 日收盘 105：
        raw_return = (105 - 100) / 100 × 100 = 5.0
        净收益按 _net_return：
            entry = 100 × (1 + 0.20%) × (1 + 0.025%) ≈ 100.225
            exit  = 105 × (1 - 0.20%) × (1 - 0.025%) × (1 - 0.05%) ≈ 104.711
            net   = (104.711 / 100.225 - 1) × 100 ≈ 4.476
    """
    from datetime import date
    from mo_stock.backtest.short_engine import _simulate_holding_period
    from mo_stock.storage.models import DailyKline, TradeCal

    sess = sqlite_session
    ts_code = "000001.SZ"
    # future_trade_dates 从 entry_date 开始（含），limit=2 → [4-2, 4-3]
    sess.add_all([
        TradeCal(cal_date=date(2026, 4, 1), is_open=True),
        TradeCal(cal_date=date(2026, 4, 2), is_open=True),
        TradeCal(cal_date=date(2026, 4, 3), is_open=True),
    ])
    # entry 日 K 线必须存在（_simulate_holding_period 会扫描 entry → exit 的全部日期）
    sess.add_all([
        DailyKline(ts_code=ts_code, trade_date=date(2026, 4, 2),
                   open=100.0, high=100.5, low=99.5, close=100.0,
                   pre_close=99.0, amount=100000.0, pct_chg=1.0),
        DailyKline(ts_code=ts_code, trade_date=date(2026, 4, 3),
                   open=100.0, high=106.0, low=100.0, close=105.0,
                   pre_close=100.0, amount=200000.0, pct_chg=5.0),
    ])
    sess.commit()

    cost_cfg = {
        "buy_slippage_pct": 0.20,
        "sell_slippage_pct": 0.20,
        "commission_pct": 0.025,
        "tax_pct": 0.05,
    }
    result = _simulate_holding_period(
        sess,
        ts_code=ts_code,
        entry_date=date(2026, 4, 2),
        entry_price=100.0,
        holding_days=2,
        stop_loss_pct=5.0,
        cost_cfg=cost_cfg,
    )
    # 返回的是 dict，不是 ORM 对象
    assert isinstance(result, dict)
    assert result["raw_return_pct"] == pytest.approx(5.0, abs=0.01)
    assert result["realized_return_pct"] == pytest.approx(5.0, abs=0.01)
    # 净收益（扣费后）应比 raw 低 ~0.5%
    assert result["net_raw_return_pct"] == pytest.approx(4.476, abs=0.05)
    assert result["net_realized_return_pct"] == pytest.approx(4.476, abs=0.05)
    # 没触发止损
    assert result["stop_hit"] is False
```

- [ ] **Step 2: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_backtest.py::test_simulate_holding_period_applies_costs_to_net_return -v`
Expected: PASS

如果 FAIL：
- "TradeCal" 找不到 → 检查 [src/mo_stock/storage/models.py](../../src/mo_stock/storage/models.py) 实际类名（可能是 `TradeCal` 或 `TradeCalendar`），相应修正
- 净收益数字差 > 0.05% → 去 [src/mo_stock/backtest/short_engine.py:381](../../src/mo_stock/backtest/short_engine.py) 看 `_net_return` 的实际公式（可能 sell_slippage 没生效或公式不同），按代码反推期望值

- [ ] **Step 3: Commit**

```bash
git add tests/unit/backtest/test_short_backtest.py
git commit -m "test: 短线回测成本扣费集成测试

确认 _simulate_holding_period 在真实 cost_cfg 下，
net_realized_return_pct 比 raw_return_pct 低约 0.5%（A 股单笔往返成本）"
```

---

## Task 3: 实现 rule_score 分档分析（TDD）

**Files:**
- Create: `src/mo_stock/backtest/calibration.py`
- Create: `tests/unit/backtest/test_short_calibration.py`

回答"综合分越高，胜率/均收益是否真的越高？"。**注意**：默认收益字段为 `net_realized_return_pct`（扣费后口径）。

- [ ] **Step 1: 写失败的测试**

Create `tests/unit/backtest/test_short_calibration.py`:

```python
"""短线策略校准分析的单元测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _make_trade(
    rule_score: float = 50.0,
    net_realized_return_pct: float | None = 1.0,
    holding_days: int = 1,
    active_dims: int = 2,
    rank_in_day: int = 1,
    dim_detail: dict | None = None,
    sector_l1: str | None = "电子",
):
    """构造测试用 trade 行（兼容 ShortBacktestTrade ORM 字段）。"""
    return SimpleNamespace(
        rule_score=rule_score,
        net_realized_return_pct=net_realized_return_pct,
        realized_return_pct=net_realized_return_pct,  # 测试便利
        holding_days=holding_days,
        active_dims=active_dims,
        rank_in_day=rank_in_day,
        dim_detail=dim_detail or {},
        sector_l1=sector_l1,
        exit_date=None,
        stop_hit=False,
    )


def test_rule_score_buckets_groups_correctly() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trades = [
        _make_trade(rule_score=15, net_realized_return_pct=2.0),
        _make_trade(rule_score=35, net_realized_return_pct=1.5),
        _make_trade(rule_score=42, net_realized_return_pct=-1.0),
        _make_trade(rule_score=55, net_realized_return_pct=3.0),
        _make_trade(rule_score=72, net_realized_return_pct=4.5),
    ]
    buckets = rule_score_buckets(trades, holding_days=1)

    assert len(buckets) == 5
    labels = [b.label for b in buckets]
    assert labels == ["[0,30)", "[30,40)", "[40,50)", "[50,60)", "[60,100]"]
    assert buckets[0].count == 1
    assert buckets[0].avg_return == pytest.approx(2.0)
    assert buckets[0].win_rate == pytest.approx(100.0)


def test_rule_score_buckets_filters_by_holding_days() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trades = [
        _make_trade(rule_score=50, net_realized_return_pct=1.0, holding_days=1),
        _make_trade(rule_score=50, net_realized_return_pct=2.0, holding_days=3),
    ]
    buckets_1d = rule_score_buckets(trades, holding_days=1)
    buckets_3d = rule_score_buckets(trades, holding_days=3)
    assert buckets_1d[3].avg_return == pytest.approx(1.0)
    assert buckets_3d[3].avg_return == pytest.approx(2.0)


def test_rule_score_buckets_uses_net_return_by_default() -> None:
    """默认应该用 net_realized_return_pct（扣费后），不是 realized_return_pct。"""
    from mo_stock.backtest.calibration import rule_score_buckets

    trade = SimpleNamespace(
        rule_score=50, holding_days=1, active_dims=2, rank_in_day=1,
        dim_detail={}, sector_l1="电子", exit_date=None, stop_hit=False,
        realized_return_pct=5.0,        # 扣费前
        net_realized_return_pct=4.5,    # 扣费后
    )
    buckets = rule_score_buckets([trade], holding_days=1)
    # 默认取 net_realized_return_pct
    assert buckets[3].avg_return == pytest.approx(4.5)


def test_rule_score_buckets_can_switch_return_field() -> None:
    from mo_stock.backtest.calibration import rule_score_buckets

    trade = SimpleNamespace(
        rule_score=50, holding_days=1, active_dims=2, rank_in_day=1,
        dim_detail={}, sector_l1="电子", exit_date=None, stop_hit=False,
        realized_return_pct=5.0,
        net_realized_return_pct=4.5,
    )
    buckets = rule_score_buckets([trade], holding_days=1,
                                  return_field="realized_return_pct")
    assert buckets[3].avg_return == pytest.approx(5.0)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'mo_stock.backtest.calibration'"

- [ ] **Step 3: 实现 calibration 模块的 rule_score_buckets**

Create `src/mo_stock/backtest/calibration.py`:

```python
"""短线策略实证校准分析。

读 short_backtest_trade 表的回测样本，输出多维度统计：
- rule_score 分档（验证综合分排序有效性）
- 催化维度数分组（不含 exhaustion，验证多维共振）
- rank_in_day 分组（缓解 picked Top N 的选股偏差）
- sector_l1 分组（找强行业/弱行业）
- 维度命中组合分析（默认排除 exhaustion）
- exhaustion 质量分档（独立做）

所有函数默认统计 net_realized_return_pct（扣费后真实净收益），可切换 return_field。
所有函数为纯函数，输入 Iterable[Any] 只要含必要字段即可，方便单测用 SimpleNamespace。

催化维度的定义：limit / moneyflow / lhb / sector / theme（不含 exhaustion）。
exhaustion 是动量质量维度，作为风险修饰，不算入"多维共振"。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean
from typing import Any

CATALYST_DIMS = frozenset({"limit", "moneyflow", "lhb", "sector", "theme"})

_BUCKET_EDGES: list[tuple[float, float, str]] = [
    (0.0, 30.0, "[0,30)"),
    (30.0, 40.0, "[30,40)"),
    (40.0, 50.0, "[40,50)"),
    (50.0, 60.0, "[50,60)"),
    (60.0, 100.01, "[60,100]"),
]


@dataclass(frozen=True)
class BucketStats:
    """一档/一组的统计结果。"""
    label: str
    count: int
    avg_return: float
    median_return: float
    win_rate: float  # %


def _filter_rows(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str,
) -> list[tuple[Any, float]]:
    """筛选指定持有期且收益字段非空的样本。返回 (trade, return_pct) 元组列表。"""
    out: list[tuple[Any, float]] = []
    for t in trades:
        if getattr(t, "holding_days", None) != holding_days:
            continue
        val = getattr(t, return_field, None)
        if val is None:
            continue
        out.append((t, float(val)))
    return out


def _bucket_stats(label: str, returns: list[float]) -> BucketStats:
    if not returns:
        return BucketStats(label=label, count=0,
                            avg_return=0.0, median_return=0.0, win_rate=0.0)
    wins = sum(1 for r in returns if r > 0)
    sorted_r = sorted(returns)
    n = len(sorted_r)
    med = sorted_r[n // 2] if n % 2 == 1 \
          else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
    return BucketStats(
        label=label,
        count=n,
        avg_return=mean(returns),
        median_return=med,
        win_rate=wins / n * 100,
    )


def rule_score_buckets(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 rule_score 把交易分 5 档，统计每档的胜率和均收益。

    默认用 net_realized_return_pct（扣费后口径）。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    result: list[BucketStats] = []
    for low, high, label in _BUCKET_EDGES:
        bucket = [
            r for t, r in rows
            if low <= float(getattr(t, "rule_score", 0) or 0) < high
        ]
        result.append(_bucket_stats(label, bucket))
    return result
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 4 个测试 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准分析新增 rule_score 分档统计

按 rule_score 分 5 档，默认用 net_realized_return_pct（扣费后）。
catalyst dims = limit/moneyflow/lhb/sector/theme（exhaustion 单独处理）"
```

---

## Task 4: 催化维度数分组（剥离 exhaustion）

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

回答"多维共振是否真的胜率更高？"。**关键修正**：active_dims 字段包含 exhaustion，会污染共振判读。这里重新从 `dim_detail` 数 `score>0` 的催化维度数（不含 exhaustion）。

- [ ] **Step 1: 追加失败测试**

```python
def test_catalyst_dims_groups_excludes_exhaustion() -> None:
    from mo_stock.backtest.calibration import catalyst_dims_groups

    trades = [
        # 1 个催化（limit），还有 exhaustion 不算
        _make_trade(net_realized_return_pct=1.0, dim_detail={
            "limit": {"score": 50}, "exhaustion": {"score": 80},
        }),
        # 2 个催化（limit + moneyflow）
        _make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
        }),
        # 3 个催化
        _make_trade(net_realized_return_pct=3.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
            "lhb": {"score": 40},
        }),
        # 5 个催化全 → 4+ 组
        _make_trade(net_realized_return_pct=4.0, dim_detail={
            "limit": {"score": 50}, "moneyflow": {"score": 30},
            "lhb": {"score": 40}, "sector": {"score": 60},
            "theme": {"score": 50},
        }),
    ]
    groups = catalyst_dims_groups(trades, holding_days=1)
    assert set(groups.keys()) == {"1", "2", "3", "4+"}
    assert groups["1"].count == 1
    assert groups["1"].avg_return == pytest.approx(1.0)
    assert groups["4+"].count == 1
    assert groups["4+"].avg_return == pytest.approx(4.0)


def test_catalyst_dims_groups_zero_score_not_counted() -> None:
    from mo_stock.backtest.calibration import catalyst_dims_groups

    trade = _make_trade(net_realized_return_pct=1.0, dim_detail={
        "limit": {"score": 50},
        "moneyflow": {"score": 0},  # score=0 不算命中
        "exhaustion": {"score": 90},  # 不算
    })
    groups = catalyst_dims_groups([trade], holding_days=1)
    # 只有 limit 命中 → 1 组
    assert groups["1"].count == 1
    assert groups["2"].count == 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_catalyst_dims_groups_excludes_exhaustion -v`
Expected: FAIL with "ImportError: cannot import name 'catalyst_dims_groups'"

- [ ] **Step 3: 在 calibration.py 追加 catalyst_dims_groups**

```python
def _count_catalyst_hits(dim_detail: dict | None) -> int:
    """从 dim_detail 数命中的催化维度数（score > 0，不含 exhaustion）。"""
    if not dim_detail:
        return 0
    return sum(
        1 for d, payload in dim_detail.items()
        if d in CATALYST_DIMS
        and isinstance(payload, dict)
        and float(payload.get("score") or 0) > 0
    )


def catalyst_dims_groups(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> dict[str, BucketStats]:
    """按催化维度数（1/2/3/4+）分组，验证多维共振假设。

    催化维度 = limit/moneyflow/lhb/sector/theme，不含 exhaustion。
    从 dim_detail 数 score > 0 的命中维度（active_dims 字段把 exhaustion 也算进去了，
    会污染共振判读）。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    groups: dict[str, list[float]] = {"1": [], "2": [], "3": [], "4+": []}
    for t, r in rows:
        n = _count_catalyst_hits(getattr(t, "dim_detail", None))
        if n <= 0:
            continue
        key = str(n) if n < 4 else "4+"
        groups[key].append(r)

    return {key: _bucket_stats(key, bucket) for key, bucket in groups.items()}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 6 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准新增催化维度数分组（剥离 exhaustion）

active_dims 字段包含 exhaustion 会污染共振判读，
改为从 dim_detail 数 limit/moneyflow/lhb/sector/theme 中 score>0 的命中数"
```

---

## Task 5: rank_in_day 分组（缓解 picked 偏差）

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

`short_backtest_trade` 只存每日 picked Top N，rule_score 分档存在选股偏差（低分进 picked 多发生在弱市）。`rank_in_day` 分组对比同一天内不同排名段的表现，是 picked 内可比的强弱信号。

- [ ] **Step 1: 追加失败测试**

```python
def test_rank_in_day_groups_returns_three_buckets() -> None:
    from mo_stock.backtest.calibration import rank_in_day_groups

    trades = [
        _make_trade(rank_in_day=1, net_realized_return_pct=3.0),
        _make_trade(rank_in_day=3, net_realized_return_pct=2.0),
        _make_trade(rank_in_day=5, net_realized_return_pct=1.5),
        _make_trade(rank_in_day=8, net_realized_return_pct=1.0),
        _make_trade(rank_in_day=15, net_realized_return_pct=0.0),
        _make_trade(rank_in_day=20, net_realized_return_pct=-0.5),
    ]
    groups = rank_in_day_groups(trades, holding_days=1)
    assert set(groups.keys()) == {"1-5", "6-10", "11-20"}
    assert groups["1-5"].count == 3
    assert groups["1-5"].avg_return == pytest.approx((3 + 2 + 1.5) / 3)
    assert groups["6-10"].count == 1
    assert groups["11-20"].count == 2
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_rank_in_day_groups_returns_three_buckets -v`
Expected: FAIL

- [ ] **Step 3: 在 calibration.py 追加 rank_in_day_groups**

```python
def rank_in_day_groups(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> dict[str, BucketStats]:
    """按 rank_in_day 分 3 段（1-5 / 6-10 / 11-20）统计。

    缓解 picked Top N 选股偏差——同一日内排名是绝对可比的，
    不依赖"低 rule_score 是否在弱市才进 picked"的混淆。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    edges = [(1, 5, "1-5"), (6, 10, "6-10"), (11, 20, "11-20")]
    groups: dict[str, list[float]] = {label: [] for _, _, label in edges}
    for t, r in rows:
        rank = int(getattr(t, "rank_in_day", 0) or 0)
        for low, high, label in edges:
            if low <= rank <= high:
                groups[label].append(r)
                break

    return {label: _bucket_stats(label, bucket) for label, bucket in groups.items()}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 7 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准新增 rank_in_day 分组（缓解 picked 偏差）

按当日排名 1-5 / 6-10 / 11-20 对比，是 picked 内可比的强弱信号。
不依赖 rule_score 分档可能存在的市场状态混淆。"
```

---

## Task 6: sector_l1 分组分析

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

回答"哪些行业是策略的强项/弱项"。按 `sector_l1`（信号日快照）分组，过滤样本数 ≥ min_count 的行业，按均收益降序排序。

- [ ] **Step 1: 追加失败测试**

```python
def test_sector_groups_filters_by_min_count_and_sorts() -> None:
    from mo_stock.backtest.calibration import sector_groups

    trades = (
        # 电子：3 条，均收益 2.0
        [_make_trade(sector_l1="电子", net_realized_return_pct=2.0)] * 3
        # 计算机：3 条，均收益 -0.5
        + [_make_trade(sector_l1="计算机", net_realized_return_pct=-0.5)] * 3
        # 食品饮料：1 条，应被 min_count 过滤
        + [_make_trade(sector_l1="食品饮料", net_realized_return_pct=5.0)]
    )
    result = sector_groups(trades, holding_days=1, min_count=3)
    labels = [b.label for b in result]
    # 应按 avg_return 降序，且不含 sample 不足的食品饮料
    assert labels == ["电子", "计算机"]
    assert "食品饮料" not in labels
    assert result[0].avg_return == pytest.approx(2.0)


def test_sector_groups_skips_none_sector() -> None:
    from mo_stock.backtest.calibration import sector_groups

    trades = [_make_trade(sector_l1=None, net_realized_return_pct=1.0)] * 5
    result = sector_groups(trades, holding_days=1, min_count=3)
    # sector_l1=None 应被跳过，不要把 None 当成一个分组
    assert result == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_sector_groups_filters_by_min_count_and_sorts -v`
Expected: FAIL

- [ ] **Step 3: 在 calibration.py 追加 sector_groups**

```python
def sector_groups(
    trades: Iterable[Any],
    holding_days: int,
    min_count: int = 20,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 sector_l1 分组，过滤样本数 < min_count 的行业，按 avg_return 降序返回。

    sector_l1 来自 short_backtest_trade 的快照列（信号日时点），
    不依赖 index_member 的当前状态，可跨时间稳定比较。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    by_sector: dict[str, list[float]] = {}
    for t, r in rows:
        sec = getattr(t, "sector_l1", None)
        if not sec:
            continue
        by_sector.setdefault(sec, []).append(r)

    result = [
        _bucket_stats(sec, bucket)
        for sec, bucket in by_sector.items()
        if len(bucket) >= min_count
    ]
    result.sort(key=lambda b: b.avg_return, reverse=True)
    return result
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 9 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准新增 sector_l1 分组分析

按申万一级行业分组，过滤样本数 < min_count 后按均收益降序排序，
找出策略的强项/弱项行业"
```

---

## Task 7: 维度命中组合分析（默认排除 exhaustion）

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

回答"哪些催化组合最有效？"。**关键修正**：默认从组合里排除 exhaustion（动量质量维度，不是催化）。

- [ ] **Step 1: 追加失败测试**

```python
def test_dim_combination_analysis_excludes_exhaustion_by_default() -> None:
    from mo_stock.backtest.calibration import dim_combination_analysis

    trades = (
        # 组合 limit+lhb，3 笔
        [_make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40},
            "exhaustion": {"score": 95},  # 应被排除，不影响组合 label
        })] * 3
    )
    combos = dim_combination_analysis(trades, holding_days=1, min_count=3)
    assert len(combos) == 1
    assert combos[0].label == "lhb+limit"  # 字母序：h < i，且不应含 exhaustion
    assert combos[0].count == 3


def test_dim_combination_analysis_filters_min_count_and_sorts() -> None:
    from mo_stock.backtest.calibration import dim_combination_analysis

    trades = (
        # 组合 A：3 笔，均 2.5
        [_make_trade(net_realized_return_pct=2.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        + [_make_trade(net_realized_return_pct=3.0, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        + [_make_trade(net_realized_return_pct=2.5, dim_detail={
            "limit": {"score": 30}, "lhb": {"score": 40}})] * 1
        # 组合 B：2 笔（< min_count 应被过滤）
        + [_make_trade(net_realized_return_pct=4.0, dim_detail={
            "moneyflow": {"score": 50}, "sector": {"score": 50}})] * 2
    )
    combos = dim_combination_analysis(trades, holding_days=1, min_count=3)
    assert len(combos) == 1
    assert combos[0].label == "lhb+limit"  # 字母序
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_dim_combination_analysis_excludes_exhaustion_by_default -v`
Expected: FAIL

- [ ] **Step 3: 在 calibration.py 追加 dim_combination_analysis**

```python
def dim_combination_analysis(
    trades: Iterable[Any],
    holding_days: int,
    min_count: int = 5,
    exclude_dims: frozenset[str] = frozenset({"exhaustion"}),
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 dim_detail 命中维度集合分组（默认排除 exhaustion），返回样本数 ≥ min_count 的组合。

    label 形如 "limit+lhb"、"moneyflow+sector+theme"，键名按字母序保证稳定。
    按 avg_return 降序返回。

    exhaustion 是动量质量维度（不是催化），混进组合 label 会让"limit+exhaustion"看起来像"双信号共振"。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    by_combo: dict[str, list[float]] = {}
    for t, r in rows:
        detail = getattr(t, "dim_detail", None) or {}
        hit_dims = sorted(
            d for d, payload in detail.items()
            if d not in exclude_dims
            and isinstance(payload, dict)
            and float(payload.get("score") or 0) > 0
        )
        if not hit_dims:
            continue
        key = "+".join(hit_dims)
        by_combo.setdefault(key, []).append(r)

    result = [
        _bucket_stats(key, bucket)
        for key, bucket in by_combo.items()
        if len(bucket) >= min_count
    ]
    result.sort(key=lambda b: b.avg_return, reverse=True)
    return result
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 11 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准维度组合分析（默认排除 exhaustion）

exhaustion 是动量质量维度，混进组合 label 会让 'limit+exhaustion'
看起来像双信号共振；改为默认 exclude_dims={'exhaustion'}"
```

---

## Task 8: exhaustion 质量分档（独立做）

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

exhaustion 单独看：按 exhaustion 分数（0-100）分 3 档，看动量质量是否真的影响后续收益。

- [ ] **Step 1: 追加失败测试**

```python
def test_exhaustion_quality_buckets_groups_by_score() -> None:
    from mo_stock.backtest.calibration import exhaustion_quality_buckets

    trades = [
        _make_trade(net_realized_return_pct=-1.0, dim_detail={
            "exhaustion": {"score": 30}}),  # 差
        _make_trade(net_realized_return_pct=1.0, dim_detail={
            "exhaustion": {"score": 60}}),  # 中
        _make_trade(net_realized_return_pct=2.0, dim_detail={
            "exhaustion": {"score": 85}}),  # 健康
        _make_trade(net_realized_return_pct=3.0, dim_detail={
            "exhaustion": {"score": 95}}),  # 健康
    ]
    buckets = exhaustion_quality_buckets(trades, holding_days=1)
    labels = [b.label for b in buckets]
    assert labels == ["差(<50)", "中(50-80)", "健康(>=80)"]
    assert buckets[0].count == 1
    assert buckets[2].count == 2
    assert buckets[2].avg_return == pytest.approx(2.5)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_exhaustion_quality_buckets_groups_by_score -v`
Expected: FAIL

- [ ] **Step 3: 在 calibration.py 追加 exhaustion_quality_buckets**

```python
def exhaustion_quality_buckets(
    trades: Iterable[Any],
    holding_days: int,
    return_field: str = "net_realized_return_pct",
) -> list[BucketStats]:
    """按 exhaustion 分数分 3 档：差(<50) / 中(50-80) / 健康(>=80)。

    回答"动量质量是否真的影响后续收益"——若健康档显著好于差档，
    说明 exhaustion 维度有判别力。
    """
    rows = _filter_rows(trades, holding_days, return_field)

    edges = [(0.0, 50.0, "差(<50)"),
             (50.0, 80.0, "中(50-80)"),
             (80.0, 100.01, "健康(>=80)")]
    groups: list[tuple[str, list[float]]] = [(label, []) for _, _, label in edges]
    for t, r in rows:
        detail = getattr(t, "dim_detail", None) or {}
        ex_score = float((detail.get("exhaustion") or {}).get("score") or 0)
        for i, (low, high, label) in enumerate(edges):
            if low <= ex_score < high:
                groups[i][1].append(r)
                break

    return [_bucket_stats(label, bucket) for label, bucket in groups]
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 12 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准新增 exhaustion 质量分档

按 exhaustion 分数 <50 / 50-80 / >=80 分档，
独立验证动量质量维度的判别力（不参与共振判读）"
```

---

## Task 9: markdown 报告渲染

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

把上面 6 类统计结果渲染成 markdown 报告。

- [ ] **Step 1: 追加失败测试**

```python
def test_render_markdown_report_includes_all_sections() -> None:
    from mo_stock.backtest.calibration import (
        BucketStats, render_markdown_report,
    )
    from datetime import date

    md = render_markdown_report(
        run_id="abc-123",
        backtest_window=(date(2025, 9, 1), date(2026, 4, 30)),
        holding_days=1,
        return_field="net_realized_return_pct",
        total_trades=70,
        rule_buckets=[BucketStats("[0,30)", 10, 0.5, 0.3, 45.0)],
        catalyst_groups={
            "1": BucketStats("1", 20, 0.2, 0.1, 40.0),
            "2": BucketStats("2", 30, 1.0, 0.8, 55.0),
            "3": BucketStats("3", 15, 1.8, 1.5, 65.0),
            "4+": BucketStats("4+", 5, 2.5, 2.3, 70.0),
        },
        rank_groups={
            "1-5": BucketStats("1-5", 35, 1.5, 1.0, 60.0),
            "6-10": BucketStats("6-10", 20, 0.8, 0.5, 50.0),
            "11-20": BucketStats("11-20", 15, 0.2, 0.0, 45.0),
        },
        sector_buckets=[BucketStats("电子", 25, 1.5, 1.0, 60.0)],
        dim_combos=[BucketStats("limit+lhb", 8, 2.0, 1.5, 62.5)],
        exhaustion_buckets=[
            BucketStats("差(<50)", 5, -0.5, -0.3, 30.0),
            BucketStats("中(50-80)", 30, 0.8, 0.5, 50.0),
            BucketStats("健康(>=80)", 35, 1.5, 1.0, 60.0),
        ],
    )
    # 关键内容都出现
    assert "# 短线策略校准报告" in md
    assert "abc-123" in md
    assert "net_realized_return_pct" in md  # 明确标注扣费后口径
    assert "[0,30)" in md
    assert "limit+lhb" in md
    assert "4+" in md
    assert "1-5" in md
    assert "电子" in md
    assert "健康(>=80)" in md
    assert "70" in md  # total_trades
    # 必须含 picked 偏差告警
    assert "picked" in md.lower() or "选股偏差" in md
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py::test_render_markdown_report_includes_all_sections -v`
Expected: FAIL

- [ ] **Step 3: 在 calibration.py 追加 render_markdown_report**

```python
def render_markdown_report(
    run_id: str,
    backtest_window: tuple,
    holding_days: int,
    return_field: str,
    total_trades: int,
    rule_buckets: list[BucketStats],
    catalyst_groups: dict[str, BucketStats],
    rank_groups: dict[str, BucketStats],
    sector_buckets: list[BucketStats],
    dim_combos: list[BucketStats],
    exhaustion_buckets: list[BucketStats],
) -> str:
    """渲染校准报告为 markdown。明确标注 return_field 口径和 picked 偏差告警。"""
    start, end = backtest_window
    L: list[str] = []

    L.append("# 短线策略校准报告")
    L.append("")
    L.append(f"- 回测批次：`{run_id}`")
    L.append(f"- 回测区间：{start} ~ {end}")
    L.append(f"- 持有期：{holding_days} 日")
    L.append(f"- 收益口径：`{return_field}`（"
             f"{'扣费后' if return_field.startswith('net_') else '扣费前'}）")
    L.append(f"- 样本总数：{total_trades}")
    L.append("")
    L.append("> **方法论限制**：当前 `short_backtest_trade` 只持久化每日 picked Top N，"
             "**不含全部 scored 候选**。rule_score 分档存在选股偏差——低分能进 picked "
             "通常发生在市场弱日（候选少导致门槛降低），与「低分股票本身」的真实表现不可分离。"
             "排名段分组（§3）是 picked 内可比的强弱信号，可做交叉验证。")
    L.append("")

    # §1 rule_score 分档
    L.append("## 1. 按 rule_score 分档")
    L.append("")
    L.append(_render_table(["分档", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                           [_row(b) for b in rule_buckets]))
    L.append("")
    L.append("**判读**：从 `[0,30)` 到 `[60,100]` 胜率和均收益应单调上升；"
             "若不单调说明 rule_score 排序失效。**结论需结合 §3 排名分组交叉验证**——"
             "如果 §3 单调而本节倒挂，更可能是 picked 偏差而非排序失效。")
    L.append("")

    # §2 催化维度数分组
    L.append("## 2. 按催化维度数分组（不含 exhaustion）")
    L.append("")
    L.append(_render_table(["命中数", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                           [_row(catalyst_groups[k]) for k in ["1", "2", "3", "4+"]]))
    L.append("")
    L.append("**判读**：多维共振假设成立时，4+ 应显著优于 1。"
             "催化维度 = limit/moneyflow/lhb/sector/theme（exhaustion 单独看）。")
    L.append("")

    # §3 rank_in_day 分组
    L.append("## 3. 按当日排名分段（picked 内可比）")
    L.append("")
    L.append(_render_table(["排名段", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                           [_row(rank_groups[k]) for k in ["1-5", "6-10", "11-20"]]))
    L.append("")
    L.append("**判读**：同一日内排名段是 picked 内可比的强弱信号，"
             "不依赖「低分是否在弱市才进 picked」的混淆。理想情况下 1-5 显著优于 11-20。")
    L.append("")

    # §4 sector_l1
    L.append("## 4. 按申万一级行业分组（样本数 ≥ 20）")
    L.append("")
    if not sector_buckets:
        L.append("（无满足样本数门槛的行业）")
    else:
        L.append(_render_table(["行业", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                               [_row(b) for b in sector_buckets]))
    L.append("")
    L.append("**判读**：找出策略的强项/弱项行业。"
             "可作为后续按行业差异化阈值或权重的依据。")
    L.append("")

    # §5 维度组合
    L.append("## 5. 维度命中组合 TOP 10（样本 ≥ 5，已排除 exhaustion）")
    L.append("")
    if not dim_combos:
        L.append("（无满足样本数门槛的组合）")
    else:
        L.append(_render_table(["组合", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                               [_row(b) for b in dim_combos[:10]]))
    L.append("")
    L.append("**判读**：找出胜率 > 60% 且样本量足够的组合，"
             "作为后续 limit / theme / sector 维度扩展的优先方向。")
    L.append("")

    # §6 exhaustion 质量分档
    L.append("## 6. 按 exhaustion 分数分档（独立做）")
    L.append("")
    L.append(_render_table(["质量档", "样本数", "胜率(%)", "均收益(%)", "中位收益(%)"],
                           [_row(b) for b in exhaustion_buckets]))
    L.append("")
    L.append("**判读**：健康(>=80)档应显著优于差(<50)档。"
             "若差档反而更好，说明 exhaustion 维度需要重新设计。")
    L.append("")

    return "\n".join(L)


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = ["---:" if i > 0 else "---" for i in range(len(headers))]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(sep) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _row(b: BucketStats) -> list[str]:
    return [
        b.label,
        str(b.count),
        f"{b.win_rate:.1f}",
        f"{b.avg_return:.2f}",
        f"{b.median_return:.2f}",
    ]
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_short_calibration.py -v`
Expected: 13 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/backtest/calibration.py tests/unit/backtest/test_short_calibration.py
git commit -m "feat: 校准 markdown 报告渲染（6 节）

明确标注 return_field 口径和 picked 偏差告警；
6 节：rule_score 分档 / 催化维度数 / rank_in_day / sector / 组合 / exhaustion 质量"
```

---

## Task 10: 校准脚本入口

**Files:**
- Create: `scripts/calibrate_short_strategy.py`

薄入口：从 DB 读最新（或指定）`backtest_run_id` 的所有 trades，调 calibration 模块，输出 markdown 文件到 `docs/`，文件名内置 `holding_days`。

- [ ] **Step 1: 创建脚本**

Create `scripts/calibrate_short_strategy.py`:

```python
#!/usr/bin/env python
"""短线策略校准脚本：读最新回测批次 → 输出校准报告 markdown。

用法：
    .venv/bin/python scripts/calibrate_short_strategy.py [--run-id UUID] [--holding-days 1]

不传 --run-id 时取 short_backtest_trade 表里最新的 backtest_run_id。
输出文件名格式：docs/short-strategy-calibration-YYYY-MM-DD-{holding_days}d.md
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import desc, func, select

from mo_stock.backtest.calibration import (
    catalyst_dims_groups,
    dim_combination_analysis,
    exhaustion_quality_buckets,
    rank_in_day_groups,
    render_markdown_report,
    rule_score_buckets,
    sector_groups,
)
from mo_stock.storage.db import get_session
from mo_stock.storage.models import ShortBacktestTrade


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None,
                        help="回测批次 UUID；不传取最新")
    parser.add_argument("--holding-days", type=int, default=1,
                        help="持有期（默认 1d）")
    parser.add_argument("--output-dir", default="docs",
                        help="报告输出目录（默认 docs/）")
    parser.add_argument("--return-field", default="net_realized_return_pct",
                        help="收益字段（默认 net_realized_return_pct，扣费后）")
    args = parser.parse_args()

    with get_session() as sess:
        run_id = args.run_id or _latest_run_id(sess)
        if not run_id:
            print("未找到任何回测批次，请先跑 mo-stock backtest --strategy short")
            return

        trades = sess.execute(
            select(ShortBacktestTrade).where(
                ShortBacktestTrade.backtest_run_id == run_id
            )
        ).scalars().all()

        if not trades:
            print(f"回测批次 {run_id} 无任何交易记录")
            return

        window = (
            min(t.signal_date for t in trades),
            max(t.signal_date for t in trades),
        )

        rule_buckets = rule_score_buckets(
            trades, holding_days=args.holding_days, return_field=args.return_field)
        catalyst = catalyst_dims_groups(
            trades, holding_days=args.holding_days, return_field=args.return_field)
        ranks = rank_in_day_groups(
            trades, holding_days=args.holding_days, return_field=args.return_field)
        sectors = sector_groups(
            trades, holding_days=args.holding_days, min_count=20,
            return_field=args.return_field)
        combos = dim_combination_analysis(
            trades, holding_days=args.holding_days, min_count=5,
            return_field=args.return_field)
        ex_buckets = exhaustion_quality_buckets(
            trades, holding_days=args.holding_days, return_field=args.return_field)

        hd_count = sum(1 for t in trades if t.holding_days == args.holding_days)

        md = render_markdown_report(
            run_id=run_id,
            backtest_window=window,
            holding_days=args.holding_days,
            return_field=args.return_field,
            total_trades=hd_count,
            rule_buckets=rule_buckets,
            catalyst_groups=catalyst,
            rank_groups=ranks,
            sector_buckets=sectors,
            dim_combos=combos,
            exhaustion_buckets=ex_buckets,
        )

    today = date.today().isoformat()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"short-strategy-calibration-{today}-{args.holding_days}d.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"校准报告已写入 {out_path}")


def _latest_run_id(sess) -> str | None:
    """取最新写入的批次。

    按 max(ShortBacktestTrade.id) 排序——同一回测区间多次重跑时，signal_date 一样
    会随机取到任意 run；id 是写入顺序，能稳定指向最近一次回测。
    """
    row = sess.execute(
        select(ShortBacktestTrade.backtest_run_id,
               func.max(ShortBacktestTrade.id))
        .group_by(ShortBacktestTrade.backtest_run_id)
        .order_by(desc(func.max(ShortBacktestTrade.id)))
        .limit(1)
    ).first()
    return row[0] if row else None


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证脚本能跑 --help 不报错**

Run: `.venv/bin/python scripts/calibrate_short_strategy.py --help`
Expected: 打印 argparse usage，含 --run-id / --holding-days / --output-dir / --return-field

如果 ImportError，去验证 `get_session` 实际位置：

```bash
grep -rn "def get_session\|@contextmanager" /Users/zsm/QuantProjects/mo-stock-selection/src/mo_stock/storage/ | head -5
```

按实际路径修正 import。`db.py` 已经在 plan v2 验证过是正确路径。

- [ ] **Step 3: Commit**

```bash
git add scripts/calibrate_short_strategy.py
git commit -m "feat: 短线策略校准脚本入口

读最新回测批次 → 调 calibration 6 类分析 → 输出 markdown 到 docs/。
文件名内置 holding_days，1d/3d 不会互相覆盖"
```

---

## Task 11: 跑历史回测 + 产出校准报告（手动）

**Files:**
- Generated: `docs/short-strategy-calibration-2026-05-10-1d.md`
- Generated: `docs/short-strategy-calibration-2026-05-10-3d.md`

- [ ] **Step 1: 检查历史数据回填到位**

```bash
.venv/bin/python -c "
from mo_stock.storage.db import get_session
from mo_stock.storage.models import DailyKline, LimitList, Lhb, Moneyflow, SwDaily, ThsDaily
from sqlalchemy import select, func
from datetime import date
with get_session() as s:
    for model, name in [(DailyKline, 'daily_kline'), (LimitList, 'limit_list'),
                         (Lhb, 'lhb'), (Moneyflow, 'moneyflow'),
                         (SwDaily, 'sw_daily'), (ThsDaily, 'ths_daily')]:
        cnt = s.execute(select(func.count()).where(model.trade_date.between(date(2025,9,1), date(2026,4,30)))).scalar()
        print(f'{name}: {cnt} rows')
"
```

如果某表数量明显偏少（< 期望 50%），先跑 `mo-stock backfill --days 240` 补数据。

- [ ] **Step 2: 跑短线回测**

```bash
.venv/bin/mo-stock backtest --strategy short --start 2025-09-01 --end 2026-04-30
```

期望耗时：~10-30 分钟。观察 stdout：每天 `picked` 数量 10-20 只、最终落库 `short_backtest_trade` 行数 5000-10000 行。

如中途报错：数据缺失 → `mo-stock backfill --days 240`；其它错误去 [src/mo_stock/backtest/short_engine.py](../../src/mo_stock/backtest/short_engine.py) 排查。

- [ ] **Step 3: 跑校准脚本，产出 1d / 3d 两份报告**

```bash
.venv/bin/python scripts/calibrate_short_strategy.py --holding-days 1
.venv/bin/python scripts/calibrate_short_strategy.py --holding-days 3
```

两份报告自动落到 `docs/short-strategy-calibration-2026-05-10-1d.md` 和 `-3d.md`，不会互相覆盖。

- [ ] **Step 4: 检查报告内容合理**

打开两份 md 目测：
- 头部明确标注 `收益口径：net_realized_return_pct（扣费后）`
- 含 picked 偏差告警段
- §1（rule_score 分档）从 `[0,30)` 到 `[60,100]` 是否单调上升？
- §2（催化维度数）4+ 是否优于 1？
- §3（rank_in_day）1-5 是否优于 11-20？— 如果 §1 倒挂但 §3 单调，picked 偏差是主因
- §4（sector）哪些行业胜率 > 60%？
- §5（组合）TOP 5 是哪些组合？
- §6（exhaustion）健康档是否优于差档？

- [ ] **Step 5: Commit**

```bash
git add docs/short-strategy-calibration-2026-05-10-1d.md docs/short-strategy-calibration-2026-05-10-3d.md
git commit -m "docs: 短线策略校准报告（2025-09 ~ 2026-04 回测）

1d/3d 两份产出，扣费后口径，含 picked 偏差告警。
6 节分析：rule_score 分档 / 催化维度 / 排名段 / 行业 / 组合 / exhaustion 质量"
```

---

## Task 12: 写决策建议文档（基于报告数据）

**Files:**
- Create: `docs/short-strategy-calibration-decisions-2026-05-10.md`

把报告里观察到的现象转化为可执行的优化建议。**必须基于真实数据**，不能用占位符。

- [ ] **Step 1: 创建决策文档骨架**

Create `docs/short-strategy-calibration-decisions-2026-05-10.md`:

```markdown
# 短线策略校准决策记录

> 基于 `short-strategy-calibration-2026-05-10-1d.md` 和 `-3d.md`（扣费后净收益口径）。
> 已知方法论限制：rule_score 分档存在 picked 偏差，结论交叉 §3 排名段验证。

## 观察 1：rule_score 分档单调性

| 分档 | 1d 胜率 | 1d 均收益 | 3d 胜率 | 3d 均收益 |
|---|---:|---:|---:|---:|
| [0,30)  | TODO  | TODO  | TODO  | TODO  |
| [30,40) | TODO  | TODO  | TODO  | TODO  |
| [40,50) | TODO  | TODO  | TODO  | TODO  |
| [50,60) | TODO  | TODO  | TODO  | TODO  |
| [60,100] | TODO | TODO  | TODO  | TODO  |

**结论**：（基于数据填写）

**与排名段交叉验证**：（§3 是否单调？如果 §1 倒挂但 §3 单调，更可能是 picked 偏差而非排序失效）

**决策**：（保持/调整权重，具体调多少）

## 观察 2：多维共振（催化维度数）

（从报告 §2 抄数）

**结论**：（4+ 是否显著高于 1？差距多少？）

**决策**：
- 4+ 显著高于 1（差距 > 1% 均收益）→ 多维共振假设成立，固定分母设计正确
- 差距很小或反向 → 考虑给单维极端分倾斜

## 观察 3：rank_in_day 分组（picked 内可比强弱）

（从报告 §3 抄数）

**结论**：1-5 vs 11-20 的差距？

**决策**：（top_n 是否需要收紧）

## 观察 4：行业差异

（从报告 §4 抄 TOP/BOTTOM 各 3 个）

**结论**：策略的强项/弱项行业

**决策**：是否需要按行业差异化阈值

## 观察 5：最有效的催化组合

（从报告 §5 抄 TOP 5）

**结论**：哪些组合胜率 > 60%？

**决策**：
- 主导组合的维度可适度加权
- 弱组合的维度在硬规则层考虑加准入门槛

## 观察 6：exhaustion 维度判别力

（从报告 §6 抄数）

**结论**：健康档 vs 差档差距

**决策**：是否调整 exhaustion 权重（当前 0.10）

## 后续 plan

基于本决策，独立编写后续 plan：

1. **B: anns_raw 数据接入** — 让 negative_announcement_keywords 硬规则真正生效
2. **C: AI 权重调整** — 评估 ai_analysis 表历史 ai_score 与实际收益的相关性，决定是否把 0.4 降到 0.2-0.3
3. **D: limit 维度扩展** — 基于 §5 维度组合数据决定是否新增"非涨停强势"子项
4. **E: 阈值调优** — 基于 §1 拐点和 §4 行业差异调整各 filter 内部分档值
5. **F: 落库全部 scored candidates** — 根本解决 rule_score 分档的 picked 偏差，需要扩展 short_engine 落库非 picked 的样本
```

- [ ] **Step 2: 阅读 1d / 3d 报告，把所有 TODO 填上真实数字**

打开两份报告，把数字填进决策文档对应的 TODO 处。

- [ ] **Step 3: 基于数据写实际结论**

每个"结论"段写 2-3 句基于数据的描述，每个"决策"段写明确的"调"或"不调"以及调多少。**不要保留"如果...则..."的占位**。

- [ ] **Step 4: Commit**

```bash
git add docs/short-strategy-calibration-decisions-2026-05-10.md
git commit -m "docs: 基于校准数据的短线策略优化决策

记录 rule_score 单调性 / 共振假设 / 排名段 / 行业 / 组合 / exhaustion 质量
六类观察的具体结论，并据此提出后续 5 份独立 plan"
```

---

## 后续独立 plan（不在本 plan 范围）

完成本 plan 后，根据 Task 12 决策文档，独立编写：

| Plan | 范围 | 依赖 |
|---|---|---|
| **B: anns_raw 数据接入** | 接入 Tushare anns_d 接口，让 `negative_announcement_keywords` 硬规则真正生效 | 独立，可并行 |
| **C: AI 权重调整** | 评估 ai_analysis 表历史 ai_score 与实际收益的相关性，决定 0.4 是否降到 0.2-0.3 | 依赖 ai_analysis 表样本 ≥ 3 个月 |
| **D: limit 维度扩展** | 新增"非涨停强势"子项（连续小阳线 5-8 天 + 超板块涨幅） | 依赖本 plan §5 维度组合数据 |
| **E: 阈值调优** | 基于 §1 rule_score 分档拐点和 §4 行业差异，调整各 filter 内部分档值 | 依赖本 plan 完成 |
| **F: 落库全部 scored candidates** | 扩展 short_engine 把非 picked 的 scored 样本也落库，根治 rule_score 分档的 picked 偏差 | 独立，与 A 并行可做 |

---

## Task 13: 补充收益结构与止损率指标（已实施）

**背景**：第一版 `BucketStats` 只有 `win_rate / avg_return / median_return`，能回答“哪组更好”，
但看不出收益结构是否健康。短线策略容易出现“多次小赚 + 一次大亏”，因此在 Task 11 实跑前补充
盈亏结构和止损率。

**Files:**
- Modify: `src/mo_stock/backtest/calibration.py`
- Modify: `tests/unit/backtest/test_short_calibration.py`

**实现内容**：
- `BucketStats` 新增：
  - `avg_win`：平均盈利
  - `avg_loss`：平均亏损（仅统计负收益样本，0 收益不拉平亏损幅度）
  - `profit_factor`：总盈利 / 总亏损绝对值；无亏损且有盈利时显示为 `∞`
  - `stop_rate`：`stop_hit=True` 的样本占比
- `rank_in_day_groups` 新增 `21+` 兜底桶，避免未来 `--top-n > 20` 时静默丢弃样本。
- 报告 §1-§6 表格统一扩展为：
  `分组 / 样本数 / 胜率 / 均收益 / 中位收益 / 平均盈利 / 平均亏损 / 盈亏因子 / 止损率`
- 新增测试覆盖：
  - `avg_win / avg_loss / profit_factor / stop_rate`
  - `rank_in_day=21/30` 进入 `21+`
  - markdown 报告包含 `盈亏因子` 和 `止损率(%)`

**判读原则**：
- `profit_factor` 样本数少时不稳定，必须结合样本数、胜率和中位收益看。
- `stop_rate` 是短线风险指标；若某组止损率明显高，说明该信号组合可能过激。
- exhaustion 质量档的判断不只看均收益，也要看胜率、中位收益、盈亏因子、止损率在 1d/3d 是否同向。

---

## Self-Review

- ✅ **Spec coverage**：覆盖"P0 跑回测校准 + P1 合理成本假设"两项；P0 anns_raw / P1 AI 权重 / P2 limit 扩展明确转为后续独立 plan
- ✅ **No placeholders**：Task 1-10 所有代码完整可执行；Task 11-12 是必须人工交互的步骤而非伪装占位
- ✅ **Type consistency**：`BucketStats` 在 Task 3 定义后，Task 4-9 全部复用；`render_markdown_report` 入参签名与 Task 10 脚本调用对齐；所有 `*_groups` 函数返回 `dict[str, BucketStats]`、所有 `*_buckets`/`*_analysis` 返回 `list[BucketStats]`
- ✅ **codex review 修正**：(1) 收益字段统一 `net_realized_return_pct`；(2) `_simulate_holding_period` API 对齐（kw-only / 返 dict / 需 entry_price + TradeCal）；(3) `get_session` 改为 `mo_stock.storage.db`；(4) 新增 `rank_in_day_groups` 缓解 picked 偏差并在报告内告警；(5) `catalyst_dims_groups` 和 `dim_combination_analysis` 默认排除 exhaustion；(6) 新增 `sector_groups` 任务；(7) 文件名内置 `holding_days` 替代 `--output-suffix`
- ⚠️ **Task 11 数据依赖**：执行时如发现历史数据缺失，需要先跑 `backfill --days 240`，已在 Step 1 写出检查命令
