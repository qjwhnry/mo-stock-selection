# `limit_restart` 维度实现方案

## 与 `limit_filter` 的职责边界

| | `limit_filter` | `limit_restart` |
|---|---|---|
| 涨停时间 | 当天涨停质量 + T-1 涨停的断板反包 | 最近涨停距今天 **2-5 个交易日** |
| 今日状态 | 当天涨停 / 昨天涨停今天反包 | 今天**不**涨停 |
| 硬规则 | 当日涨停 → hard_reject 淘汰（全局） | 不新增全局硬规则；今日涨停仅在本维度内跳过，不产出分数 |

**核心约束**：以"最近一次涨停日期"为准，不是"过去 2-5 天内至少一次"。若某股 T-3 涨停、T-1 又涨停，最近一次涨停在 T-1，交给 `limit_filter` 断板反包，`limit_restart` 不打分。

---

## 模式定义

```
T-5 ~ T-2: 最近一次涨停（催化剂识别）
T-1:       缩量回调/整固（可选）
T (今天):  缩量企稳或放量重启 → 收盘后识别 → 选入
T+1:       开盘买入
T+2~T+3:   主升兑现 → 卖出
```

---

## 权重调整

| 维度 | 现权重 | 新权重 | 变化 |
|------|--------|--------|------|
| limit | 0.25 | **0.18** | -0.07 |
| moneyflow | 0.25 | **0.22** | -0.03 |
| lhb | 0.20 | **0.15** | -0.05 |
| sector | 0.10 | 0.10 | — |
| theme | 0.10 | 0.10 | — |
| exhaustion | 0.10 | 0.10 | — |
| **limit_restart** | — | **0.15** | +0.15 |

limit + limit_restart 合计 0.33。limit 的 0.18 大部分打在当日涨停股上（全被 hard_reject），真正产出可买入候选的是 limit_restart 的 0.15 + limit 断板反包的残留部分。权重 0.15 标记为**待回测校准**——上线后用回测观察 `limit_restart` 单维和 `limit_restart + moneyflow/theme/sector` 组合的胜率与收益。

---

## 打分逻辑

### Step 1：候选筛选

- 最近一次涨停距今天 2-5 个交易日（查询 `limit_list`）
- T-1 涨停的股票不进入（归 `limit_filter` 断板反包）
- 今日涨停股在 `limit_restart` 内直接跳过，不产出本维度分数；若由其它维度进入候选，仍由 hard_reject 最终淘汰
- 非 ST / 非次新（由 hard_reject 层处理）

### Step 2：回调质量（上限 45 分）

| 子项 | 满分 | 逻辑 |
|------|------|------|
| 缩量程度 | 20 | 今日量 vs 涨停日量：`ratio = today_vol / limit_day_vol`。≤ 0.3 → 20；0.3~0.5 → 15；0.5~0.7 → 8；> 0.7 → 0 |
| 不破支撑 | 15 | 看 **涨停日后至今日的区间最低价**：`min_low_since_limit ≥ 涨停日 close` → 15；`min_low_since_limit` 在 [涨停日 open, close) → 8；`min_low_since_limit < 涨停日 open` → 0。注意是区间最低价，不是今日 low —— 中间破位、今天拉回不算真支撑。后续回测若大量有效票只拿 8 分需校准 |
| 回调天数 | 10 | 距最近涨停 2 天 → 10；3 天 → 8；4 天 → 5；5 天 → 3 |

### Step 3：重启信号（上限 55 分）

| 子项 | 满分 | 逻辑 |
|------|------|------|
| 收阳 | 15 | close > open → 15；close > pre_close 且 close ≤ open（假阴真阳）→ 8；收跌 → 0 |
| 量能恢复 | 15 | 今日量 vs 前 5 个交易日均量（不含今日）：`ratio = today_vol / avg(prev_5_trade_days_vol)`。≥ 1.5 → 15；1.0~1.5 → 10；0.7~1.0 → 5；< 0.7 → 0 |
| 重心上移 | 15 | close 在今日振幅位置：上 1/3 → 15；中 1/3 → 8；下 1/3 → 0。若 `high <= low` 或关键 OHLC 缺失，此项给 0 分 |
| 低开高走 | 10 | open < pre_close 且 close > open → 10 |

### Step 4：惩罚项（正数扣减）

| 子项 | 扣分 | 条件 |
|------|------|------|
| 涨停日量异常 | 20 | `limit_day_vol / 涨停日前20日均量` > 3（出货嫌疑）。均量不含涨停日本身。若涨停日前有效交易日样本不足 10 天，不触发此惩罚，detail 标记 `insufficient_prev20_volume: true` |
| 多涨停衰减 | `max(limit_count - 1, 0) * 15` | 统计范围：最近 5 个交易日（不含今日，含最近涨停日）的涨停天数 |

```
总分 = clamp(回调质量 + 重启信号 - 惩罚项, 0, 100)
```

### detail 关键字段（供 AI prompt / 报告翻译使用）

| 字段 | 说明 |
|------|------|
| `volume_vs_limit_day` | 今日量 / 涨停日量 |
| `volume_vs_prev5` | 今日量 / 前 5 个交易日均量（不含今日） |
| `limit_day_volume_vs_prev20` | 涨停日量 / 涨停日前 20 日均量 |
| `insufficient_prev20_volume` | 涨停日前有效样本不足 10 天时为 true，跳过异常量惩罚 |
| `min_low_since_limit` | 涨停日后至今日的区间最低价 |
| `limit_date` | 最近一次涨停日期 |
| `days_since_limit` | 距最近涨停天数 |
| `support_level` | 支撑位判定结果（full / partial / broken） |
| `limit_count_5d` | 近 5 个交易日涨停次数 |

---

## 已确认的设计决策

1. **回溯窗口 2-5 天**：A 股涨停后回调通常在 2-5 天内完成，1 天太短无法确认趋势，超过 5 天催化剂效应消散。
2. **T-1 不纳入**：归 `limit_filter` 断板反包，避免重复计分。
3. **缩量两个口径分离**：`volume_vs_limit_day`（回调程度）和 `volume_vs_prev5`（重启程度）是两个独立维度，不混合。
4. **支撑位 15/8/0**：先不改，回测后校准。
5. **今日跌幅不加硬规则**：支撑位锚定比固定百分比更贴近形态。
6. **不破支撑看区间最低价**：`min(涨停日后至今日 low)`，不是今日 low。中间破位今天拉回不算真支撑。
7. **量能恢复分母不含今日**：`today_vol / avg(前 5 个交易日 vol)`，避免今日放量被自己稀释。
8. **涨停日前 20 日均量不足 10 天**：跳过异常量惩罚，标记 `insufficient_prev20_volume`。
9. **惩罚项正数扣减**：`base - penalty`，detail 中惩罚值存正数。
10. **权重 0.15 待回测校准**：上线后用短线回测验证 `limit_restart` 命中组合的胜率收益。

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `config/weights.yaml` | dim_weights 调整 + 新增 `limit_restart_filter` 配置节 |
| `src/mo_stock/filters/short/limit_restart_filter.py` | **新建**：LimitRestartFilter 实现 |
| `src/mo_stock/filters/short/__init__.py` | 导出 LimitRestartFilter |
| `src/mo_stock/scheduler/daily_job.py` | `_build_filters` 注册新维度 |
| `src/mo_stock/scorer/combine.py` | `_SHORT_ADMISSION_DIMS` 加 `limit_restart` |
| `src/mo_stock/backtest/short_engine.py` | 回测引擎注册新维度 |
| `src/mo_stock/analyzer.py` | 单股分析注册新维度 |
| `src/mo_stock/ai/prompts.py` | AI prompt 维度翻译加 `limit_restart` |
| `src/mo_stock/report/render_md.py` | 报告维度中文展示加 `limit_restart` |
| `docs/scoring.md` | 更新为 7 维短线口径 |
| `tests/` | 新增 `test_limit_restart_filter.py`（T-2 重启 / T-5 边界 / T-1 不纳入 / T-3+T-1 双涨停不纳入 / 今日涨停跳过 / min_low_since_limit 中间破位不拿支撑分 / 异常量惩罚 / 多涨停衰减 / 零振幅重心上移给 0 分） |
