# 短线透支检测维度（ExhaustionFilter）设计文档

> 版本：v1.0 | 日期：2026-05-09 | 状态：已实现
>
> 关联 issue：短线策略 1-3 天回看周期过短，缺少"是否已经涨太多了"的系统性检查。

---

## 一、问题定义

### 1.1 现状

短线策略 5 个维度（limit / moneyflow / lhb / sector / theme）的共性是：**只问"最近有没有好事发生"**。

| 维度 | 回看周期 | 问的问题 |
|------|---------|---------|
| limit | 2 天 | 昨天涨停 → 今天断板反包？ |
| moneyflow | 1-3 天 | 今天主力净流入了吗？3 日累计为正吗？ |
| lhb | 1 天 | 今天上榜了吗？席位结构如何？ |
| sector | 1-3 天 | 今天板块涨幅排第几？3 日均涨如何？ |
| theme | 1-2 天 | 今天概念涨幅排第几？昨天也在榜吗？ |

**没有任何一个维度问"最近 5-10 天是不是涨太多了？"**

### 1.2 风险场景

一只股票连涨 8 天累计 +35%，第 9 天小幅回调但资金流仍为正 → 以高分入选 → 持仓 1-3 天后继续回调，亏损出场。

这违反了短线策略的核心假设：**买在短期动量启动点，而非动量尾声**。

### 1.3 设计目标

1. 系统性检测短期超涨、量价背离、动量衰减等"透支"信号
2. 梯度惩罚（0-100 连续分），而非二值淘汰，保留区分度
3. 极端透支仍可硬淘汰，但由独立规则层控制
4. 不影响现有 5 个维度的内部逻辑，完全解耦
5. 参数可热调（通过 weights.yaml），无需改代码

---

## 二、方案选型

### 2.1 候选方案对比

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A. 在各维度内加长周期检查 | 在 limit/moneyflow 等内部加 5 日涨幅判断 | 改动分散 | 职责混乱，各维度管自己的"反向信号"违背单一职责 | ❌ |
| B. 只在 combine 加 hard_reject | 在 `_build_hard_reject_map` 加二值淘汰规则 | 实现简单 | 二值化丢失梯度，轻度透支和严重透支无法区分 | ❌ |
| C. 新增独立透支维度 | 新增 ExhaustionFilter，替代 sentiment 预留位 | 职责清晰、梯度惩罚、可配置 | 新文件、新测试 | ✅ |

选择 C。理由：

- "短期透支"是一个独立的风险因子，和"有没有资金流入"回答不同的问题
- 梯度惩罚：轻度透支降权但不打死，极端透支在 hard_reject 层淘汰
- 替代当前预留但未实现的 `sentiment` 权重位（0.10），权重总和保持 1.00，不破坏固定分母结构
- 未来如需接入真正的 sentiment，可以调权重分配，不影响 exhaustion

### 2.2 为什么不做 RSI

RSI（6 日 / 14 日）是经典超买指标，但在本方案中被跳过：

- RSI 本质是涨幅和跌幅的比值变换，我们的 5 日累计涨幅 + 连涨天数 + 动量衰减已经捕获了 RSI 的核心信息
- 多一个指标 = 多一重共线性风险（与 5 日涨幅高度相关）
- 保持信号集最小完备，不做过度参数化

---

## 三、信号设计

### 3.1 总体框架

```
新鲜度分 = clamp(100 - Σpenalties, 0, 100)
```

- 100 = 完全新鲜，无任何透支信号
- 0 = 严重透支，全部信号同时触发且到上限
- `ExhaustionFilter` 可对所有活跃 A 股出分，但综合层不把它作为候选准入信号：
  短线候选仍必须至少命中 `limit / moneyflow / lhb / sector / theme` 之一，
  `exhaustion` 只作为这些候选的风险修正与解释证据。
- 下列 5 个信号的阈值、惩罚上限和分档比例均从 `config/weights.yaml`
  的 `exhaustion_filter` 节读取；本章数字是当前默认值，完整配置清单见 5.3。

### 3.2 信号 1：5 日累计涨幅（主信号，最高扣 35 分）

**设计依据**：5 个交易日覆盖约 1 个完整交易周，比 3 日更能发现"调整后重新上涨但整体已高"的股。

```
ret_5d = (close_today - close_T_minus_5) / close_T_minus_5 × 100

penalty（默认配置）:
  ret_5d ≤ threshold_5d_return_low(5%)  → 0
  5% < ret_5d < threshold_5d_return_high(25%) → 线性插值 0 → penalty_5d_return_max(35)
  ret_5d ≥ 25% → 35（满罚）
```

**阈值理由**：
- 5%：A 股正常周波动范围，不应惩罚
- 25%：非连板情况下极罕见（≈ 2.5 个涨停），属于极端透支
- 线性插值而非分档：提升同板块内个股区分度（与 moneyflow v2.4 连续化设计一致）

### 3.3 信号 2：MA5 偏离度（最高扣 25 分）

**设计依据**：短线持仓 1-3 天，MA5 是最相关的短期均值锚。偏离太远 → 均值回归压力大。

```
deviation = (close_today - MA5) / MA5 × 100

penalty（默认配置）:
  deviation ≤ threshold_ma5_deviation_low(2%) → 0
  2% < deviation < threshold_ma5_deviation_high(8%) → 线性 0 → penalty_ma5_deviation_max(25)
  deviation ≥ 8% → 25（满罚）
```

**为什么是 MA5 而非 MA10/MA20**：
- MA10 更适合 5-10 天持仓（介于短线和波段之间）
- MA20 是波段策略的锚（已被 RiskLiquidityFilter 使用）
- MA5 与 1-3 天持仓周期精确匹配

**去共线机制**：MA5 偏离与 5 日涨幅高度正相关（涨得多 → MA5 必然低于现价）。
当 5 日涨幅惩罚已触发时，MA5 偏离惩罚自动 ×0.5，避免两个信号对同一事实重复惩罚。

### 3.4 信号 3：冲高回落（最高扣 15 分）

**设计依据**：短线持仓 1-3 天时，近期上涨后的长上影容易代表盘中卖压或获利盘兑现。
但上影线占比单独使用会被窄幅震荡放大，因此必须同时确认当天 high 相对昨收确实冲高。

```
upper_shadow_ratio = (high - max(open, close)) / (high - low)
close_position = (close - low) / (high - low)
ret_3d = (close_today - close_T_minus_3) / close_T_minus_3 × 100
high_return_from_pre_close = (high - pre_close) / pre_close × 100

触发条件（四者同时满足）：
  upper_shadow_ratio > threshold_upper_shadow_ratio(0.6)
  close_position < threshold_close_position(0.5)
  ret_3d > threshold_upper_shadow_3d_return(5.0)
  high_return_from_pre_close >= threshold_high_return_from_pre_close(3.0)

惩罚 = upper_shadow_ratio × penalty_upper_shadow_max × close_penalty_factor
close_penalty_factor = 1.0 + (threshold_close_position - close_position) × 0.5
最终 clamp 到 0 ~ penalty_upper_shadow_max
```

`high_return_from_pre_close` 用于过滤窄幅假上影：如果 today high 相对昨收涨幅不足 3%，
即使上影线占比因 `high - low` 分母过小而超过 60%，也不视为真正冲高回落。

### 3.5 信号 4：量价背离（最高扣 20 分）

两种模式：

**模式 A — 缩量上涨**：
```
条件：近 3 日涨幅 > threshold_shrink_3d_return(5%) AND 今日成交量 < 近 5 日均量的 threshold_shrink_vol_ratio(60%)
惩罚 = severity × penalty_volume_divergence_max
  severity = (threshold_shrink_vol_ratio - vol_ratio) / threshold_shrink_vol_ratio  // 量越缩越严重
```

**模式 B — 放量滞涨**：
```
条件：今日成交量 > 近 5 日均量的 threshold_blowoff_vol_ratio(2倍) AND pct_chg（相对昨收）< threshold_blowoff_pct_chg(2%)
惩罚 = penalty_volume_divergence_max × 0.8
```
使用 pct_chg（相对昨收）而非日内涨幅：放量但未能有效收复昨收价（默认涨幅 < 2%），
视为滞涨或弱反抽风险。包括微跌场景——放量下跌同样是高位换手信号。

两种模式取 max（不累加），因为它们描述的风险场景不同但互斥。

### 3.6 信号 5：连涨天数（最高扣 10 分）

**设计依据**：A 股散户市中，连涨天数本身是反转概率的有效代理变量。

```
判定：close > open（收阳线）计为"涨日"
从最新一天往回数连续阳线天数

penalty（默认配置）:
  0-2 天 → 0
  3-4 天 → penalty_consecutive_up_max × ratio_consecutive_up_low(0.35)
  5-6 天 → penalty_consecutive_up_max × ratio_consecutive_up_mid(0.70)
  7+ 天 → penalty_consecutive_up_max × 1.00（满罚）
```

**为什么用 close > open 而非 pct_chg > 0**：
- 假阳线（低开高走但相对昨收仍跌）在短期内仍代表当日买盘强于卖盘
- 连阳从市场心理角度比连涨更有信号意义

### 3.7 信号 6：动量衰减（最高扣 10 分）

**设计依据**：5 日涨幅高不等于透支——关键是涨幅发生在什么时候。

```
条件：ret_5d > threshold_decay_5d_return(10%) AND ret_2d < ret_5d × threshold_decay_2d_ratio(0.2)
触发 → 惩罚 penalty_momentum_decay_max(10) 分（二值）

其中 `ret_2d = close_today / close_T_minus_2 - 1`，使用两个完整交易日窗口，
避免误把单日休整当成动量衰减。

示例：
  5 日 +20%，近 2 日 +2% → 90% 的涨幅发生在 3-5 天前 → 触发
  5 日 +20%，近 2 日 +12% → 60% 的涨幅在近 2 天 → 不触发（动量健康）
```

### 3.8 硬规则（极端透支直接淘汰）

在 `hard_reject` 层加两条二值淘汰规则，与梯度惩罚互补：

```yaml
# 1. 5 日涨幅 > 25% → 直接淘汰（约 2.5 个涨停）
max_5d_return_pct: 25

# 2. 5 日涨幅 > 20% 且连涨 ≥ 5 天 → 直接淘汰
max_consecutive_up_with_5d_return:
  ret_5d_pct: 20
  min_consecutive_days: 5
```

两条规则可独立配置或置 0 关闭。

---

## 四、权重与打分影响分析

### 4.1 权重分配

```yaml
dimension_weights:
  limit: 0.25
  moneyflow: 0.25
  lhb: 0.20
  sector: 0.10
  theme: 0.10
  exhaustion: 0.10  # 替代原 sentiment 预留位
```

### 4.2 对 rule_score 的影响

`exhaustion = 0.10` 替代 `sentiment = 0.10`（原始终为 0）：

| 股票类型 | 旧 rule_score | 新 rule_score | 变化 |
|---------|-------------|-------------|------|
| 正常股（exh ≈ 95） | 45 | 45 + 95×0.10 = 54.5 | +9.5 |
| 轻度透支（exh ≈ 60） | 45 | 45 + 60×0.10 = 51.0 | +6.0 |
| 严重透支（exh ≈ 25） | 45 | 45 + 25×0.10 = 47.5 | +2.5 |

**关键**：正常股比严重透支股多拿了 7 分（9.5 - 2.5），在固定分母（1.00）下足以改变排序。
但这只对已经命中正向短线信号的候选股生效；单独的高 `exhaustion` 分不会让一只
没有催化的股票进入候选池。

### 4.3 极端场景验证

| 场景 | 5 日涨幅 | MA5 偏离 | 量价 | 连涨 | 动量 | 总惩罚 | 新鲜度分 |
|------|---------|---------|------|------|------|--------|---------|
| 完美新股 | 2% | 1% | 无 | 1天 | 无 | 0 | 100 |
| 温和上涨 | 8% | 3.5% | 无 | 3天 | 无 | 5.3+3.1+3.5=11.9 | 88.1 |
| 强势但合理 | 15% | 6% | 无 | 4天 | 无 | 17.5+8.3+3.5=29.3 | 70.7 |
| 危险 | 22% | 7.5% | 缩量上涨 | 6天 | 触发 | 29.8+11.5+15+7+10=73.2 | 26.8 |
| 极端 | 28% | 9% | 放量滞涨 | 8天 | 触发 | 35+12.5+16+10+10=83.5 | 16.5 |

注：当 5 日涨幅惩罚已触发时，MA5 偏离惩罚按 0.5 折扣计入，上表已体现该去共线机制。

---

## 五、实现清单

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/mo_stock/filters/short/exhaustion_filter.py` | ExhaustionFilter + 冲高回落/量价背离等纯辅助函数 |
| `tests/unit/filters/short/test_exhaustion_filter.py` | 单元测试覆盖纯辅助函数、自定义阈值、冲高回落边界 |
| `tests/integration/test_exhaustion_admission.py` | 5 个集成测试，覆盖候选准入、排序影响和 analyzer admission 状态 |

### 5.2 修改文件

| 文件 | 变更 |
|------|------|
| `config/weights.yaml` | `sentiment` → `exhaustion`；新增 `exhaustion_filter` 配置节；新增 hard_reject 透支规则 |
| `src/mo_stock/cli.py` | 短线 pipeline 加入 ExhaustionFilter；dims 5→6 |
| `src/mo_stock/scorer/combine.py` | `_build_hard_reject_map` 新增 `_add_overextension_rejects`；新增 short 候选准入判断，`exhaustion` 不单独入池；score=0 的 exhaustion 仍透传给 AI / 报告 |
| `src/mo_stock/scheduler/daily_job.py` | 定时任务短线 pipeline 加入 ExhaustionFilter，避免与 CLI 口径分叉 |
| `src/mo_stock/backtest/short_engine.py` | 短线回测加入 ExhaustionFilter，并复用同一候选准入逻辑 |
| `src/mo_stock/analyzer.py` | 单股分析加入 ExhaustionFilter，保持调试输出与 run-once 一致 |
| `src/mo_stock/report/render_md.py` | 新增 `_translate_exhaustion`；更新 `_DIM_ORDER`/`_DIM_LABELS`/`_DIM_TRANSLATORS` |
| `src/mo_stock/ai/prompts.py` | 方法论 prompt：5 维 → 6 维，新增 exhaustion 说明 |
| `src/mo_stock/filters/base.py` | 注释更新 |
| `src/mo_stock/storage/models.py` | dim 字段注释更新 |

### 5.3 配置热调点

所有触发阈值、惩罚上限和分档比例集中在 `config/weights.yaml` 的
`exhaustion_filter` 节，调整后重跑 `run-once` 即可生效，无需改代码。
当前共 25 个配置项：

| 配置项 | 信号 | 默认值 |
|--------|------|--------|
| `penalty_5d_return_max` | 5 日涨幅惩罚上限 | 35 |
| `threshold_5d_return_low` | 5 日涨幅不罚阈值 | 5.0 |
| `threshold_5d_return_high` | 5 日涨幅满罚阈值 | 25.0 |
| `penalty_ma5_deviation_max` | MA5 偏离惩罚上限 | 25 |
| `threshold_ma5_deviation_low` | MA5 偏离不罚阈值 | 2.0 |
| `threshold_ma5_deviation_high` | MA5 偏离满罚阈值 | 8.0 |
| `penalty_upper_shadow_max` | 冲高回落惩罚上限 | 15 |
| `threshold_upper_shadow_ratio` | 冲高回落上影线占比阈值 | 0.6 |
| `threshold_close_position` | 冲高回落收盘位置阈值 | 0.5 |
| `threshold_upper_shadow_3d_return` | 冲高回落 3 日涨幅门槛 | 5.0 |
| `threshold_high_return_from_pre_close` | high 相对昨收冲高门槛 | 3.0 |
| `penalty_volume_divergence_max` | 量价背离惩罚上限 | 20 |
| `threshold_shrink_vol_ratio` | 缩量上涨量比阈值 | 0.6 |
| `threshold_shrink_3d_return` | 缩量上涨 3 日涨幅阈值 | 5.0 |
| `threshold_blowoff_vol_ratio` | 放量滞涨量比阈值 | 2.0 |
| `threshold_blowoff_pct_chg` | 放量滞涨涨幅阈值 | 2.0 |
| `penalty_consecutive_up_max` | 连涨天数惩罚上限 | 10 |
| `threshold_consecutive_up_low` | 连涨轻罚起点 | 3 |
| `threshold_consecutive_up_mid` | 连涨中罚起点 | 5 |
| `threshold_consecutive_up_high` | 连涨满罚起点 | 7 |
| `ratio_consecutive_up_low` | 连涨轻罚比例 | 0.35 |
| `ratio_consecutive_up_mid` | 连涨中罚比例 | 0.7 |
| `penalty_momentum_decay_max` | 动量衰减惩罚上限 | 10 |
| `threshold_decay_5d_return` | 动量衰减 5 日涨幅门槛 | 10.0 |
| `threshold_decay_2d_ratio` | 动量衰减近 2 日占比门槛 | 0.2 |

---

## 六、后续工作

1. **阈值回测校准**（Phase 2.5）：用历史数据验证 5 个惩罚上限的区分度和胜率贡献，按回测结果调优
2. **AI 利用率观察**：检查 AI 分析报告中对 exhaustion 维度 detail 的引用频率和解读质量
3. **sentiment 维度**：未来接入新闻/公告情绪时，可将 `exhaustion: 0.10` 拆分为 `exhaustion: 0.05` + `sentiment: 0.05`，或保持现有权重另行调整

---

## 七、Review 修正记录（2026-05-09）

CR 指出 4 个问题，已全部修正：

| # | 问题 | 修正 |
|---|------|------|
| 1 | 5 日涨幅和 MA5 偏离共线性 — 两个信号对同一事实重复惩罚 | 当 5d 惩罚 > 0 时，MA5 偏离惩罚自动 ×0.5（代码在 `score_all` 中，不影响纯函数单测） |
| 2 | hard_reject 单条阈值 30% 偏高 — 约 3 个涨停才触发，放行过多风险股 | `max_5d_return_pct: 30` → `25`（约 2.5 个涨停），组合规则（20%+5 连阳）不变 |
| 3 | 放量滞涨用日内涨幅而非相对昨收 — 低开高走但收跌的股被漏判 | 改用 `pct_chg`（close vs `klines[-2].close`），语义更准确 |
| 4 | `_penalty_volume_divergence` 缺少单元测试 — 5 个信号中最复杂却无覆盖 | 新增 10 个测试用例，覆盖缩量上涨、放量滞涨、边界、双重触发、缺失数据 |

## 八、二次 Review 修正记录（2026-05-09）

二次 CR 指出 4 个实现风险，已修正：

| # | 问题 | 修正 |
|---|------|------|
| 1 | `exhaustion` 给全市场正分，会把“没有短线催化、只是没透支”的股票拉入候选池 | 综合层新增 short 候选准入逻辑：必须至少命中 `limit / moneyflow / lhb / sector / theme` 之一；`exhaustion` 只参与已准入候选的综合分 |
| 2 | CLI 已接入，但 scheduler / short backtest / analyze 仍是旧 5 维口径 | 三处全部加入 ExhaustionFilter，并复用综合层候选准入和 score=0 透传规则 |
| 3 | 5 日、2 日窗口实现偏短：`klines[-5]` 实际更像 5 根 K 线首尾涨幅，`klines[-2]` 实际是 1 日涨幅 | 统一为 T-5 / T-2 口径：5 日涨幅用 `klines[-6]`，近 2 日动量用 `klines[-3]`；hard_reject 同步修正 |
| 4 | AI prompt 把缺失 exhaustion detail 解释为”无透支风险”，而 score=0 可能被过滤 | `exhaustion` 即使 score=0 也透传给 AI / 报告；prompt 改为”缺失只代表本次未提供该维度数据” |

## 九、三次 Review 修正记录（2026-05-09）

GPT-5.5 CR 指出 3 级优先级共 6 项问题，已全部修正：

### P1（文档一致性）

| # | 问题 | 修正 |
|---|------|------|
| 1 | `docs/scoring.md` 仍为”5 维 + sentiment”，与代码 6 维口径不一致 | scoring.md / architecture.md / cli.md / CLAUDE.md 全部更新为 6 维 + exhaustion |

### P2（代码与测试）

| # | 问题 | 修正 |
|---|------|------|
| 1 | `_penalty_momentum_decay` 中 `10.0` 和 `0.2` 硬编码，不在 cfg 热调范围内 | 改为 `cfg.get(“threshold_decay_5d_return”, 10.0)` 和 `cfg.get(“threshold_decay_2d_ratio”, 0.2)`；weights.yaml 已有对应配置项 |
| 2 | 缺少端到端语义测试验证准入不变量 | 新增 `tests/integration/test_exhaustion_admission.py`：5 个测试覆盖 exhaustion-only 不入池、moneyflow+exhaustion=0 仍准入、exhaustion 分改变排序、analyzer admission 状态 |

### P3（文档更新）

| # | 问题 | 修正 |
|---|------|------|
| 1 | 旧文档（architecture.md, scoring.md, cli.md）仍引用 sentiment / 5 维 | 已全部更新为 6 维 + exhaustion（由 agent 批量处理） |

### 5.3 配置热调点更新

所有 5 个信号的触发阈值、惩罚上限和分档比例均已配置化到
`config/weights.yaml` 的 `exhaustion_filter` 节；完整 20 项见上文 5.3。

## 十、四次 Review 修正记录（2026-05-09）

DeepSeek CR 指出 3 项文档与代码不一致问题，均为文档口径修正，代码无需变更：

| # | 问题 | 修正 |
|---|------|------|
| 1 | 4.3 极端场景表未计入 MA5 去共线折扣 | 按 `penalty_5d > 0` 时 MA5 惩罚 ×0.5 重算总惩罚和新鲜度分 |
| 2 | 5.1 测试数量仍写 24 个单元测试 | 已更新为覆盖纯函数、冲高回落边界和准入语义的单元/集成测试 |
| 3 | 3.2-3.6 信号描述容易被理解为硬编码阈值 | 明确所有阈值、惩罚上限和比例来自 `config/weights.yaml`，章节中的数字仅为默认值 |
