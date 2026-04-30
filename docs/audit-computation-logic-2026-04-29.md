# 计算逻辑审计与优化建议（2026-04-29）

## 结论

当前项目的计算主链路已经比旧审计时更稳健：

- `LimitFilter` 已删除旧版“板块涨停热度溢出”，避免 `limit` 维度重复表达板块强势。
- `combine_scores` 已加入板块多样化 cap，默认同一申万一级板块最多入选 4 只。
- `selection_result` 已改为每日全量替换，降低历史脏结果残留风险。
- `replace_filter_scores` 会按交易日和维度先删后写，适合重跑历史日期。

不过，仍有几处计算准确性、解释一致性和性能方面的优化点。建议优先处理 P0 / P1 项。

## 当前计算链路简述

主流程：

```text
ingest
  -> LimitFilter / MoneyflowFilter / LhbFilter / SectorFilter / ThemeFilter
  -> replace_filter_scores
  -> combine_scores
  -> hard_reject
  -> sector cap
  -> selection_result
  -> report
```

规则分公式：

```text
rule_score = Σ(dim_score * weight) / Σ(all_dimension_weights)
```

当前 `dimension_weights` 包含：

| 维度 | 权重 | 状态 |
|---|---:|---|
| limit | 0.25 | 已实现 |
| moneyflow | 0.25 | 已实现 |
| lhb | 0.20 | 已实现 |
| sector | 0.10 | 已实现 |
| theme | 0.10 | 已实现 |
| sentiment | 0.10 | 未实现 |

## 已确认做得较好的点

### 1. 固定分母综合分是合理的

固定分母会把缺失维度按 0 处理，可以抑制单一维度高分股票霸榜，符合多因子共振思路。

### 2. 已移除 `limit` 与 `sector` 的明显共线性

旧版 `LimitFilter` 中的“同板块涨停热度溢出”会让整个板块所有非涨停股获得高分，和 `SectorFilter` 重复表达同一信号。当前代码已删除该机制，方向正确。

### 3. 板块 cap 是必要的组合层约束

`combine.max_stocks_per_sector: 4` 可以防止最终 Top 20 被单一行业占满。这个约束应保留，并建议在重跑历史报告后验证效果。

### 4. 结果表全量替换降低脏数据风险

`selection_result` 每日先删后写，比单纯 upsert 更符合“每日快照”的语义，可以避免旧 picked 结果残留。

## P0：建议优先修复

### P0-1：断板反包使用自然日，节假日后会漏信号

位置：

- `src/mo_stock/filters/short/limit_filter.py:49`
- `src/mo_stock/filters/short/limit_filter.py:50`

当前逻辑：

```python
yesterday = trade_date - timedelta(days=1)
yesterday_limit_codes = repo.get_limit_up_codes(session, yesterday)
```

问题：

- 周一会查周日。
- 节假日后会查非交易日。
- 断板反包是 `LimitFilter` 当前真正能进入 Top N 的核心产出，漏掉上一交易日涨停会直接影响选股结果。

建议：

使用已有交易日历函数：

```python
from mo_stock.data_sources.calendar import previous_trading_day

prev_trade_date = previous_trading_day(session, trade_date)
if prev_trade_date is None:
    logger.warning("LimitFilter: {} 找不到上一交易日，跳过断板反包", trade_date)
    yesterday_limit_codes = set()
else:
    yesterday_limit_codes = repo.get_limit_up_codes(session, prev_trade_date)
```

并补充测试：

- 周一识别上周五涨停后的断板反包。
- 春节 / 国庆长假后识别节前最后一个交易日涨停。

### P0-2：单股分析漏跑 `ThemeFilter`

位置：

- `src/mo_stock/analyzer.py:88`
- `src/mo_stock/analyzer.py:93`

当前 `analyze_stock` 只跑：

```python
LimitFilter
MoneyflowFilter
LhbFilter
SectorFilter
```

问题：

主流程 `run-once` 已经包含 `ThemeFilter`，且 `theme` 权重为 0.10。单股分析漏掉该维度，会导致：

- `mo-stock analyze` 输出的维度和报告不一致。
- 单股 `rule_score` 低估。
- 用户调试单股时容易误判。

建议：

在 `analyzer.py` 中引入并执行 `ThemeFilter`，保持与 `run-once` 一致。

## P1：计算准确性与鲁棒性优化

### P1-1：AI 动态 prompt 的成交额单位错误

位置：

- `src/mo_stock/ai/analyzer.py:147`
- `src/mo_stock/ai/analyzer.py:148`

当前逻辑：

```python
# amount 是元；除以 1e8 转亿元
amount_yi = (kline.amount / 1e8) if (kline and kline.amount) else None
```

问题：

项目中 `DailyKline.amount` 遵循 Tushare `daily.amount` 口径，单位是“千元”，不是“元”。当前转换会把成交额亿元低估 1000 倍。

建议：

```python
# daily_kline.amount 单位是千元，转换为亿元：千元 * 1000 / 1e8 = / 1e5
amount_yi = (kline.amount / 1e5) if (kline and kline.amount) else None
```

影响：

该问题不影响纯规则分，但会影响 AI 对成交活跃度、流动性和短线承接的判断。

### P1-2：AI 默认启用时，未配置 Key 会中断主流程

位置：

- `src/mo_stock/ai/client.py:62`
- `src/mo_stock/ai/client.py:64`
- `src/mo_stock/ai/analyzer.py:84`

当前 `ClaudeClient` 初始化时如果没有 `ANTHROPIC_API_KEY`，会抛出 `ValueError`。但 `analyze_stock_with_ai` 中创建 client 的代码不在 try-except 保护内。

问题：

`run-once` 默认 `enable_ai=not skip_ai`。如果环境未配置 Claude Key，主流程可能直接失败，而不是降级为纯规则模式。

建议二选一：

1. `run-once` 默认跳过 AI，生产环境显式开启 AI。
2. 在 `analyze_stock_with_ai` 中把 `_get_claude_client()` 放进 try-except，失败时返回 `None`，让 `combine_scores` 按现有逻辑降级为规则分。

### P1-3：`sentiment` 未实现但占 10% 权重

位置：

- `config/weights.yaml:13`

问题：

`sentiment` 维度未实现，但固定分母中仍占 0.10。这样所有规则分的理论上限会从 100 降到 90。

这对排序影响不大，因为所有股票都同样缺失该维度；但对分数解释有影响：

- 用户看到 45 分，实际可能是“已实现维度下的 50 分”。
- AI 融合时，规则分绝对值偏低。

建议：

短期把 `sentiment` 权重设为 0，并按现有 5 个维度重新归一：

```yaml
dimension_weights:
  limit: 0.28
  moneyflow: 0.28
  lhb: 0.22
  sector: 0.11
  theme: 0.11
  sentiment: 0.00
```

或在报告中明确展示：

```text
sentiment 未接入，当前规则层理论满分为 90。
```

### P1-4：最终排序缺少显式 tiebreaker

位置：

- `src/mo_stock/scorer/combine.py:277`

当前逻辑：

```python
scored.sort(key=lambda x: x["final_score"], reverse=True)
```

问题：

在强板块行情中，大量股票可能同分。Python 排序稳定，最终顺序会继承数据库查询返回顺序，业务解释性不足。

建议：

增加确定性排序规则，例如：

```python
scored.sort(
    key=lambda x: (
        -x["final_score"],
        -x["rule_score"],
        x["ts_code"],
    )
)
```

更进一步，可以加入：

- 有效维度数更多优先。
- 资金流分更高优先。
- 非同板块候选优先。
- 成交额 / 换手率作为流动性 tiebreaker。

## P2：性能与配置一致性优化

### P2-1：`MoneyflowFilter` 存在 N+1 查询

位置：

- `src/mo_stock/filters/short/moneyflow_filter.py:103`

当前每只股票都会调用：

```python
repo.get_moneyflow_series(session, row.ts_code, trade_date, days=3)
```

问题：

全市场资金流有几千行时，会产生几千次查询。数据量不大时还能接受，但日常运行会拖慢 filter 阶段。

建议：

批量读取最近 3 个交易日的 `moneyflow`：

1. 先用交易日历获取最近 3 个交易日。
2. 一次查询这些日期的全部 `Moneyflow`。
3. 在内存中按 `ts_code` 聚合 `rolling_sum`。

### P2-2：`limit_filter.broken_board_rebound_bonus` 配置未实际使用

位置：

- `config/weights.yaml:52`
- `src/mo_stock/filters/short/limit_filter.py:214`

问题：

配置中有：

```yaml
broken_board_rebound_bonus: 10
```

但实际断板反包打分在 `_break_board_rebound_bonus` 中硬编码为：

```text
1% - 3%: 30
3% - 5%: 50
5% - 8%: 70
>= 8%: 100
```

建议：

删除无效配置，或把分档迁移到 `weights.yaml`，避免“配置可热调”与实际代码不一致。

### P2-3：历史报告需要用当前逻辑重跑

当前已有报告文件：

- `data/reports/2026-04-24.json`
- `data/reports/2026-04-27.json`

静态读取结果显示旧报告仍存在明显板块集中：

```text
2026-04-24: 801030.SI × 12, 801050.SI × 6, unknown × 2
2026-04-27: 801080.SI × 20
```

这说明报告文件没有用当前 v2.3 逻辑重跑。建议修复 P0 后执行：

```bash
mo-stock run-once --date 2026-04-24 --skip-ingest --skip-ai
mo-stock run-once --date 2026-04-27 --skip-ingest --skip-ai
```

然后检查：

- Top 20 是否至少覆盖 4-5 个申万一级板块。
- 同分股票是否仍大量存在。
- 被 sector cap 跳过的股票是否有合理 `reject_reason`。

## 建议处理顺序

1. 修复 `LimitFilter` 上一交易日判断。
2. 修复 `analyze_stock` 漏跑 `ThemeFilter`。
3. 修复 AI 成交额单位。
4. 增加 AI 未配置时的自动降级。
5. 增加排序 tiebreaker。
6. 优化 `MoneyflowFilter` 近 3 日资金流批量查询。
7. 调整或说明 `sentiment` 未实现权重。
8. 重跑历史报告并补充回归测试。

## 测试建议

建议新增或补充以下测试：

| 测试 | 目的 |
|---|---|
| 周一断板反包 | 确认使用上一交易日而不是自然日前一天 |
| 长假后断板反包 | 确认春节 / 国庆后不漏信号 |
| analyze 包含 theme | 确认单股分析与 run-once 维度一致 |
| AI 成交额单位 | 构造 `amount=100000` 千元，断言 prompt 中为 `1.0` 亿元 |
| 无 Anthropic Key 降级 | 确认 `run-once` 不因缺少 Key 中断 |
| 同分排序稳定 | 多只同分股重复 combine，顺序稳定 |
| sector cap 回归 | 单板块高分股票超过 4 只时，最终 picked 不超过 cap |

## 本次验证限制

本次审计主要基于项目文档、源码和已有报告 JSON 的静态审阅。此前只读环境下无法写入文件；切换为可写环境后已将本文档落盘。

---

## Claude Code 交叉验证（2026-04-29）

以下由 Claude Code 逐项阅读源码后独立验证，确认 Codex 审计结论**全部准确**，并补充若干细节。

### 逐项验证结果

| 编号 | Codex 结论 | 验证 | 关键源码证据 |
|------|-----------|------|-------------|
| P0-1 | 断板反包用自然日 | **确认** | `limit_filter.py:50` — `yesterday = trade_date - _ONE_DAY`；无交易日历调用 |
| P0-2 | 单股分析漏 ThemeFilter | **确认** | `analyzer.py:88-97` — 仅建 LimitFilter / MoneyflowFilter / LhbFilter / SectorFilter 四个实例 |
| P1-1 | AI 成交额单位错误 | **确认** | 见下方专项分析 |
| P1-2 | AI 未配 Key 会崩溃 | **确认** | `client.py:64` — `raise ValueError`，`ai/analyzer.py` 中 client 初始化异常未被捕获 |
| P1-3 | sentiment 占 10% 空权重 | **确认** | `_weighted_combine` 使用全量 `dim_weights.values()` 作分母，sentiment 恒贡献 0 |
| P1-4 | 排序无 tiebreaker | **确认** | `combine.py:277` — `scored.sort(key=lambda x: x["final_score"], reverse=True)` 单键排序 |
| P2-1 | MoneyflowFilter N+1 | **确认** | `moneyflow_filter.py:104` — 循环内逐股调 `repo.get_moneyflow_series` |
| P2-2 | 配置项未使用 | **确认** | `weights.yaml:52` 有 `broken_board_rebound_bonus: 10`，`_break_board_rebound_bonus` 硬编码 30/50/70/100 |

### P1-1 补充：成交额单位错误比表面更严重

Codex 指出 `DailyKline.amount` 是千元、AI 模块误当元处理。源码交叉验证发现这**不是单位约定模糊，而是明确的复制粘贴错误**：

| 位置 | 注释 | 单位判断 |
|------|------|---------|
| `models.py:118` | `comment="成交额（千元）"` | 正确 |
| `analyzer.py:155` | `# 千元` | 正确 |
| `moneyflow_filter.py:53` | `# 一次拉取当日全部 daily_kline.amount（千元）` | 正确 |
| `moneyflow_filter.py:143` | `moneyflow.net_mf_amount 是万元，daily_kline.amount 是千元` | 正确 |
| `ai/analyzer.py:147` | `# amount 是元` | **错误** — 唯一一处写错 |

项目中 4 处注释正确标注了千元，唯独 AI 模块写"元"。写 AI 模块时可能参考了龙虎榜 `TopList.amount`（那是元），但误用到了 `DailyKline.amount`（这是千元）。

**影响量化**：一笔 50 亿元成交额（`amount=500000` 千元）会被转成 `500000 / 1e8 = 0.005` 亿元显示给 AI，实际应为 `5.0` 亿元。AI prompt 中的成交额**全部被缩小 1000 倍**，会严重干扰 AI 对流动性、承接力的判断。

### P1-2 补充：降级策略建议

当前行为：`run-once` 默认 `enable_ai = not skip_ai`。未配 Key 时 `ClaudeClient.__init__` 抛 `ValueError`，向上传播到 `combine_scores` 的 `for ts_code in ai_candidates` 循环，导致整个 `run-once` 中断。

推荐方案：在 `analyze_stock_with_ai` 入口处加保护，而不是改默认值：

```python
def analyze_stock_with_ai(...):
    try:
        client = _get_claude_client()
    except ValueError:
        logger.warning("AI 模块未配置，跳过 AI 分析")
        return None
    ...
```

这样 `combine_scores` 的 `if ai_obj is not None` 分支自然走降级，**无需改动 `combine_scores`** 即可实现 graceful degradation。

### P1-3 补充：sentiment 权重的两种处理思路

Codex 建议短期把 sentiment 权重设为 0 并重新归一。还有另一种等价思路：**不改权重，但在报告中注明**。

两种方案对比：

| 方案 | 优点 | 缺点 |
|------|------|------|
| 权重归一（Codex 建议） | 分数直观，满分 100 | sentiment 接入时要再改权重 + 历史数据不连续 |
| 保留原权重 + 报告注明 | 零代码改动，sentiment 接入无缝 | 用户需知道"45 分 ≈ 已实现维度的 50 分" |

倾向保留原权重：排序不受影响（所有股同样缺失），且 v2.4 接入 sentiment 后无需再改配置。但应在 `render_md` 报告中加一行：

```
注：当前规则层仅 5 维度有效（sentiment 待接入），理论满分为 90。
```

### P1-4 补充：推荐的 tiebreaker 组合

Codex 建议的 `(-final_score, -rule_score, ts_code)` 是最小可行方案。更进一步，建议加入维度数和资金流分作为辅助排序：

```python
scored.sort(key=lambda x: (
    -x["final_score"],
    -x["rule_score"],
    -len(dim_scores_map.get(x["ts_code"], {})),  # 有效维度多者优先
    x["ts_code"],                                  # 兜底确定性
))
```

### P2-2 补充：配置清理建议

`broken_board_rebound_bonus: 10` 在 `weights.yaml` 中存在但从未被代码读取。有两种处理方式：

1. **删除无效配置**（推荐）：避免误导用户以为"断板反包加分可热调"
2. **迁移到代码**：把 30/50/70/100 分档移入 `weights.yaml`，真正支持热调

当前硬编码的 30/50/70/100 档位设计合理（覆盖 1~8%+ 的涨幅梯度），短期内无需调参。建议先删除无效配置项，等有调参需求时再迁移。

### 总体评价

Codex 审计质量高，问题定位精确，优先级划分合理。建议的修复顺序（P0 → P1 → P2）可以直接执行。唯一的补充是 P1-1 的严重程度可以上调——1000 倍的单位误差会直接影响 AI 分析质量，建议在修完 P0 后优先处理。
