# 板块集中度审计报告（2026-04-28）

## 背景

近两日选股结果出现严重板块集中：

| 日期 | 板块分布 | 备注 |
|------|---------|------|
| 2026-04-27 | **801080.SI（电子元器件）× 20** | 全部 20 只来自同一板块 |
| 2026-04-24 | 801030.SI × 12 + 801050.SI × 6 + 未知 × 2 | 仍严重不均衡 |

用户最初问"是否仅缺少板块上限"，深入排查后确认：**远不止"无限制"，存在多重叠加 bug**。

---

## 一、真正根因：多个维度重复计入"板块强势"信号（多重共线性）

抽取 2026-04-27.json 中 20 只股的各维度得分：

| 维度 | 得分 | 来源 |
|------|------|------|
| `limit` | **60** | `sector_heat_bonus`：同板块涨停 ≥10 只 → +60（板块层面信号，给同板块所有股） |
| `sector` | **70** | TOP1 板块 → +70（板块层面） |
| `theme` | **50** | 热门板块对应概念也热 → +50（板块层面） |
| `moneyflow` | **70** | 板块爆发时同板块股普遍命中 today_bonus 50 + rolling_bonus 20 |

→ 14 / 20 只股 `rule_score` 完全相同 = **44.50**。

设计意图是"5 维独立 → 共振"，但实际**1 个板块强势同时拉高 4 维**，固定分母惩罚机制也无法发挥作用。

### Bug #1 — 维度多重共线性（最严重）

涉及代码：

| 文件 | 行 | 问题 |
|------|----|------|
| `src/mo_stock/filters/limit_filter.py` | 163-172 | `sector_heat_bonus` 给同板块所有非涨停股加 60 分（板块层面） |
| `src/mo_stock/filters/sector_filter.py` | 79-84 | 给 TOP1 板块所有股 +70（板块层面） |
| `src/mo_stock/filters/theme_filter.py` | — | 同概念集体加分，与 sector 高度相关 |
| `src/mo_stock/filters/moneyflow_filter.py` | — | 板块爆发时同板块股普遍落入同一档位 |

**结论**：4 个"独立维度"实际上都在反映同一个信号"板块强势"，违背多因子选股的正交假设。

---

## 二、Bug #2 — 得分粒度过粗，平局过多

- `_sector_limit_heat_bonus`（limit_filter.py:256-270）：4 档阶梯（≥10 → 60，≥5 → 40，≥3 → 25，1-2 → 10）
- `_rank_to_bonus`（sector_filter.py:128-132）：板块排名 1-5 固定档位
- `moneyflow` `today_bonus=50, rolling_bonus=20`：命中阈值即满分，无连续过渡

实际数据印证：

```
rank  code         rule    limit  mf   sector  theme
1     300602.SZ   44.50    60    70    70      50
2     002475.SZ   44.50    60    70    70      50
3     300458.SZ   44.50    60    70    70      50
... (14 / 20 都是 44.50)
```

在板块整体爆发时，**个股微观差异完全被档位抹平**，AI 分成为唯一区分器。

---

## 三、Bug #3 — 完全无板块多样化约束

涉及代码：`src/mo_stock/scorer/combine.py:232-245`

```python
# Line 233: 纯按 final_score 排序
scored.sort(key=lambda x: x["final_score"], reverse=True)

# Lines 238-245: 贪心选 Top N，无板块判断
for item in scored:
    reject_reason = item["reject_reason"]
    picked = reject_reason is None and picked_rank < top_n  # ← 无板块 cap
    if picked:
        picked_rank += 1
        ...
```

`config/weights.yaml` 中 `sector_filter.top_n_sectors: 5` 控制的是**加分策略**，**不是最终持仓约束**。

---

## 四、Bug #4 — 平局 tiebreaker 隐式

- `combine.py:233` 使用 Python 稳定排序
- 同分股的顺序由 `stock_scores` dict 的插入序决定
- 而 `stock_scores` 来自 `FilterScoreDaily` 的查询，无显式排序 → 实际几乎一定**同板块股扎堆**

---

## 五、修复建议（按优先级）

### 优先级 1 — 去共线性（治本）

**目标**：让 5 维真正独立，反映不同信号。

可选方案：
- **方案 A**：`limit_filter` 中的 `sector_heat_bonus` 改为**惩罚因子**——板块涨停股越多，单股贡献越小；让"板块热度"只在 `sector` 维度反映
- **方案 B**：去掉 `limit_filter` 中的 `sector_heat_bonus`，`limit` 维度只保留个股层面信号（断板反包、首封、连板等）
- **方案 C**：维度间引入相关性惩罚，对每股的 5 维得分做正交化（PCA 或类似）后再加权

**推荐**：方案 B（最简单、最直接）。

### 优先级 2 — 加板块多样化 cap（治标）

修改：`src/mo_stock/scorer/combine.py:232-245`

```yaml
# config/weights.yaml 新增
sector_filter:
  max_stocks_per_sector: 4   # 最终 Top N 中每板块最多入选数
```

```python
# combine.py 在排序后、选 Top N 前加入贪心去重
sector_counts: dict[str, int] = {}
for item in scored:
    sector = item.get("sector_code")  # 需从 dim_scores_map 透传
    sector_counts[sector] = sector_counts.get(sector, 0) + 1
    if sector_counts[sector] > max_per_sector:
        continue  # 跳过本股，不计入 picked
    picked = reject_reason is None and picked_rank < top_n
    ...
```

回归测试：单板块主导日（如 2026-04-27）时验证最终 Top N 来自至少 5 个不同板块。

### 优先级 3 — 平滑得分（减少平局）

- `moneyflow` 改用 z-score 或百分位连续打分
- `sector_heat_bonus` 同样改连续函数（如 `min(60, count × 5)`）
- 引入个股微观 tiebreaker（成交额、换手率、波动率）

### 优先级 4 — 平局 tiebreaker 显式化

`combine.py:233` 改为：

```python
scored.sort(key=lambda x: (-x["final_score"], -x["turnover_rate"], x["ts_code"]))
```

或引入板块多样性的 `bucket sort` 思路。

---

## 六、影响范围

- **影响版本**：v2.1（5 维接通后）至今所有报告
- **影响严重度**：HIGH —— 选股结果实质上等价于"押注 1-2 个板块"，违背多因子分散原则
- **AI 层无法兜底**：AI 看到的证据本身就指向同一板块（"申万 TOP 1 板块 +70 分"），输出 ai_score 也接近，无法纠偏

---

## 七、附：复现命令

```python
# 检查任一报告的板块分布
python3 -c "
import json
from collections import Counter
with open('data/reports/2026-04-27.json') as f:
    data = json.load(f)
sectors = [s['dimensions'].get('sector',{}).get('detail',{}).get('l1_code') for s in data]
print(Counter(sectors))
"
```

---

## 八、Codex 复核意见（2026-04-28）

### 8.1 总体结论

本审计的大方向成立：2026-04-27 的 Top 20 确实全部来自 `801080.SI`，且大量股票在多个规则维度上得分完全相同，说明问题不是单纯"缺少板块上限"，而是：

1. 多个维度同时奖励同一个板块热度；
2. 规则分档较粗，导致同板块股票大量平分；
3. 最终 Top N 没有组合层面的行业约束；
4. 同分排序缺少显式 tiebreaker。

用现有报告文件复核结果：

```text
2026-04-24: 801030.SI × 12, 801050.SI × 6, unknown × 2
2026-04-27: 801080.SI × 20
```

其中 2026-04-27 前 20 名里，多数股票维度结构为：

```text
limit=60, moneyflow=70, sector=70, theme=50
```

这验证了"板块强势信号在多个维度重复体现"的核心判断。

### 8.2 已确认成立的代码问题

#### 问题 A：`limit` 维度混入板块热度，形成最直接共线性

代码位置：`src/mo_stock/filters/limit_filter.py:163-172`

`LimitFilter` 对所有同板块非涨停股加入 `sector_heat_bonus`。当同一申万一级行业涨停数 `>=10` 时，`_sector_limit_heat_bonus()` 直接返回 `60`：

```python
if limit_count >= 10:
    return 60
```

这使得 `limit` 维度不再只是个股涨停/断板/反包信号，而是重复表达"该板块今天很热"。这是本次集中度问题中最明确、最应该优先处理的共线性来源。

#### 问题 B：`sector` 维度天然是整板块统一加分

代码位置：`src/mo_stock/filters/sector_filter.py:78-84`

`SectorFilter` 命中 TOP1 申万一级行业时加 `70` 分，所有属于该行业的股票都会获得同样的 `rank_bonus`。这个逻辑本身符合板块维度定义，但与 `limit_filter.sector_heat_bonus`、`theme`、资金流阈值叠加后，会显著放大同板块股票的排序优势。

#### 问题 C：最终组合没有板块 cap

代码位置：`src/mo_stock/scorer/combine.py:232-245`

当前逻辑只按 `final_score` 降序排序，然后贪心取 Top N：

```python
scored.sort(key=lambda x: x["final_score"], reverse=True)

for item in scored:
    reject_reason = item["reject_reason"]
    picked = reject_reason is None and picked_rank < top_n
```

没有任何行业或板块层面的最大持仓约束。因此在单一板块整体高分时，系统会自然选出一整个板块。

#### 问题 D：同分排序没有显式规则

代码位置：`src/mo_stock/scorer/combine.py:160-196`、`src/mo_stock/scorer/combine.py:232-233`

`FilterScoreDaily` 查询没有 `ORDER BY`，而最终排序只使用 `final_score`。Python sort 是稳定排序，所以同分时会保留前序列表顺序；但前序顺序来自数据库返回顺序，并不是明确的业务规则。

这不会单独导致板块集中，但会让同分股票的最终排名不可解释、不可稳定复核。

### 8.3 需要修正或补充的原审计表述

#### 修正 1：`moneyflow` 不是板块层面信号

原文说 `moneyflow` 是板块爆发时的板块层面信号，这个表述不够准确。

代码位置：`src/mo_stock/filters/moneyflow_filter.py:77-109`

`MoneyflowFilter` 计算的是个股级别信号，包括：

- 个股主力净流入占成交额比例；
- 个股大单/超大单净流入比例；
- 个股近 3 日累计主力净流入。

更准确的结论是：`moneyflow` 本身是个股维度，但在板块整体爆发时，同板块股票容易同步跨过同一分档阈值，导致得分结果与板块热度高度相关。

#### 修正 2：`theme` 与 `sector` 高相关，但不应直接视为同一信号

代码位置：`src/mo_stock/filters/theme_filter.py:71-89`

`ThemeFilter` 基于同花顺概念、涨停最强概念、概念资金流取最高概念分。它不是申万行业维度，理论上可以捕捉跨行业题材。

但在电子、半导体这类强主题日，THS 概念和 SW 一级行业会高度重叠，因此会放大集中度。建议表述为"高相关信号"，而不是直接等同于 sector。

#### 修正 3：板块 cap 伪代码不能先计数再判断

原建议里的伪代码：

```python
sector_counts[sector] = sector_counts.get(sector, 0) + 1
if sector_counts[sector] > max_per_sector:
    continue
picked = reject_reason is None and picked_rank < top_n
```

这个顺序有问题：被硬规则淘汰、未入选、或 Top N 之外的股票也会消耗板块名额。

正确逻辑应当是：

```python
sector = sector_map.get(item["ts_code"])
sector_count = sector_counts.get(sector, 0)

over_sector_cap = (
    sector is not None
    and max_per_sector > 0
    and sector_count >= max_per_sector
)

picked = (
    reject_reason is None
    and not over_sector_cap
    and picked_rank < top_n
)

if picked:
    picked_rank += 1
    sector_counts[sector] = sector_count + 1
```

也就是说：只有实际入选的股票才应该增加 `sector_counts`。

#### 修正 4：板块 cap 不应只依赖 `dim_scores_map["sector"].detail`

原文提到 `sector_code` 需要从 `dim_scores_map` 透传。这个方案不够稳。

原因：`SectorFilter` 只对 `score > 0` 的股票写入 `FilterScoreDaily`，非热点板块股票可能没有 `sector` 维度 detail。因此最终组合层如果要做板块 cap，应直接使用 `repo.get_index_member_l1_map(session)` 获取全市场股票到申万一级行业的映射。

### 8.4 修复优先级调整建议

建议把"去共线性"和"板块 cap"都提升为 P0，在同一次修复中完成。

原因：只移除 `limit_filter.sector_heat_bonus` 后，2026-04-27 这批股票仍可能保留 `moneyflow=70 + sector=70 + theme=50` 的组合优势。按当前权重粗算，典型股票规则分会从：

```text
60 * 0.25 + 70 * 0.25 + 70 * 0.10 + 50 * 0.10 = 44.50
```

下降为：

```text
0 * 0.25 + 70 * 0.25 + 70 * 0.10 + 50 * 0.10 = 29.50
```

但如果其他板块没有足够多维共振，最终 Top N 仍可能明显集中。因此组合层 cap 是必要的风险控制，不只是临时补丁。

推荐优先级：

1. P0：移除 `limit_filter` 中的 `sector_heat_bonus`，让 `limit` 回归个股涨停/断板/反包信号；
2. P0：在 `combine_scores` 最终选股阶段加入 `max_stocks_per_sector`；
3. P1：为同分排序增加显式 tiebreaker；
4. P1：增加集中度统计与回归测试；
5. P2：把 `moneyflow`、`theme`、`sector` 的分档逐步平滑化。

### 8.5 配置位置建议

`max_stocks_per_sector` 更适合放在 `combine` 配置下，而不是 `sector_filter` 下。

原因：`sector_filter.top_n_sectors` 是打分逻辑参数；`max_stocks_per_sector` 是最终组合约束，属于 `combine_scores` 的选择阶段。

建议配置：

```yaml
combine:
  rule_weight: 0.6
  ai_weight: 0.4
  max_stocks_per_sector: 4
```

如果后续需要更细，可以扩展为：

```yaml
combine:
  max_stocks_per_sector: 4
  max_unknown_sector_stocks: 2
```

### 8.6 回归测试建议

建议至少新增以下测试场景：

1. 单板块 20 只高分股 + 其他板块若干低分股，验证最终 Top N 中单板块不超过 `max_stocks_per_sector`；
2. 高分股里混入硬规则淘汰股，验证被淘汰股不消耗板块 cap；
3. `sector=None` 的股票单独处理，避免 unknown 行业无限进入或错误挤占正常行业名额；
4. 多只股票 `final_score` 相同，验证排序结果由显式 tiebreaker 决定。

### 8.7 统计检查代码建议

原复现命令可以确认问题，但建议扩展输出集中度指标，而不仅是 `Counter`：

```python
import json
from collections import Counter
from pathlib import Path

path = Path("data/reports/2026-04-27.json")
data = json.loads(path.read_text())

sectors = [
    item.get("dimensions", {})
    .get("sector", {})
    .get("detail", {})
    .get("l1_code", "UNKNOWN")
    for item in data
    if item.get("rank", 0) > 0
]

counter = Counter(sectors)
total = len(sectors)
hhi = sum((count / total) ** 2 for count in counter.values()) if total else 0
max_share = max(counter.values()) / total if total else 0

print(counter)
print(f"sector_count={len(counter)}")
print(f"max_share={max_share:.2%}")
print(f"hhi={hhi:.4f}")
```

建议验收标准：

```text
Top20 max_share <= 20%   # max_stocks_per_sector=4 时
Top20 sector_count >= 5
Top20 HHI 明显低于修复前
```

其中 `max_share` 和 `sector_count` 是硬约束检查，`HHI` 更适合作为趋势观察指标。

---

## 九、整体总结（双方对齐后的最终方案）

### 9.1 确认的核心问题（双方一致）

1. **`limit_filter.sector_heat_bonus` 是最直接的共线性源** —— 板块涨停 ≥10 → +60，给同板块所有股 → 与 `sector` 维度重复表达"板块热"
2. **`sector` 维度 TOP1 +70** 本身设计如此，但与 limit/theme 叠加后放大集中
3. **`combine_scores` 完全无板块 cap**
4. **同分无显式 tiebreaker** —— 查询无 ORDER BY + Python 稳定排序 → 结果不可解释

### 9.2 Codex 纠正的 4 处不严谨

| # | 原审计表述 | Codex 修正 | 影响 |
|---|------------|-----------|------|
| 1 | "moneyflow 是板块层面信号" | moneyflow 实际是**个股**信号（净流入占比、大单比、3 日累计），但因**档位粗** + **板块爆发同步过阈值** → 与板块热度高度相关 | 表述更准确 |
| 2 | "theme 与 sector 同信号" | theme 基于 THS 概念，理论上跨行业；但电子/半导体日 THS 概念与 SW 行业重叠严重 → 应表述为"高相关"而非"等同" | 表述更准确 |
| 3 | cap 伪代码先计数后判断 | **错误**：被硬规则淘汰 / Top N 外的股会消耗板块名额。正确做法：**仅入选股增加 sector_counts** | **实现关键** |
| 4 | sector 来自 `dim_scores_map.sector.detail` | **不稳**：非热点板块股 `score=0` 不写入 FilterScoreDaily，detail 缺失 → 应直接用 `repo.get_index_member_l1_map(session)` | **实现关键** |

### 9.3 最终修复方案

**P0（一次性完成，本次实施）**

理由：仅移除 `sector_heat_bonus` 时 rule_score 仍有 29.50，板块仍可能集中 → cap 是必需的风控，不只是临时补丁。

1. 删除 `limit_filter.py:163-172` 的 `sector_heat_bonus` 逻辑（`limit` 维度回归个股层面）
2. `combine.py` 加入板块 cap：仅入选股消耗名额、用 `repo.get_index_member_l1_map` 获取板块映射
3. 配置放在 `config/weights.yaml` 的 `combine` 节（**非** `sector_filter` 节），默认 `max_stocks_per_sector: 4`
4. 同步更新单元测试 + 集成测试，加入 4 个回归场景

**P1（下次迭代）**

5. 显式 tiebreaker（成交额 / 换手率 / ts_code）
6. 集中度指标统计（HHI / max_share / sector_count）落库或日志化

**P2（长期优化）**

7. moneyflow / theme / sector 分档逐步平滑化（z-score / 百分位）

### 9.4 评价

Codex 的复核非常有价值——3 处实现细节修正（cap 时机、sector 数据源、配置位置）若不修，按原方案实现会**直接引入 bug**。两份审计互补：

- **原审计**：问题图谱 + 现象证据 + 量化数据
- **Codex 复核**：实现严谨性 + 验收标准 + 测试场景

按 P0 方案动手实施。

---

## 十、实施记录与后续修复（2026-04-28）

### 10.1 P0 首轮实施

按第 9 节方案完成：
- `src/mo_stock/filters/limit_filter.py`：删除 `sector_heat_bonus` + `_sector_limit_heat_bonus` + 相关 docstring
- `src/mo_stock/scorer/combine.py`：新增 `combine_cfg` 参数、板块 cap、用 `repo.get_index_member_l1_map` 取板块映射、仅入选股消耗名额
- `src/mo_stock/storage/repo.py`：删除 unused `get_l1_limit_count_map`
- `src/mo_stock/cli.py`：读 `cfg["combine"]` 并传入
- `config/weights.yaml`：新增 `combine.max_stocks_per_sector: 4`
- `tests/integration/test_combine_sector_cap.py`：新增 4 个回归场景

测试：364 → 367 passed。

### 10.2 Codex 第二轮 CR 三个补丁

| 严重度 | 问题 | 修复 |
|--------|------|------|
| HIGH | `persist_filter_scores` 仅 upsert，重跑历史日期会保留旧 `sector_heat_bonus` 脏分数 | 新增 `replace_filter_scores(session, trade_date, dims, results)`，先 `DELETE WHERE trade_date AND dim IN (...)` 再 upsert；CLI 与 scheduler 都改用此函数 |
| MEDIUM | `scheduler/daily_job.py` 没传 `combine_cfg` → 生产环境配置无效 | 增加 `combine_cfg = cfg.get("combine", {})` 并传入 `combine_scores` |
| MEDIUM | `sector_map` 为空时 cap 静默失效（所有股都 unknown 绕过 cap） | 空时打 WARNING；新增 `max_unknown_sector_stocks` 配置作为兜底 |

新增 3 个回归测试：`TestEmptySectorMap` + `TestReplaceFilterScores` (×2)。

测试：367 → 367 passed（替换式持久化不影响业务断言）。

### 10.3 Codex 第三轮 CR：HIGH—`selection_result` 也需要全量替换

**根因链**（已验证成立）：

1. `combine.py:215-217` 只把 `score > 0` 的维度纳入 `stock_scores` → 全 0 分股从 `combined` 起就消失
2. `combine.py:316-345`（旧）rows 仅 append `picked or rule_score > 0` → 幽灵股不入 rows
3. `combine.py:347-362`（旧）仅 upsert，不删 → `selection_result` 表里旧 `picked=True` 永久残留
4. `render_md.py:40` 只查 `picked=True` → 报告污染成立

**触发场景**：旧版 `sector_heat_bonus` 让 GHOST.SH 入选 `picked=True, rank=5`；v2.3 升级后 `replace_filter_scores` 清掉脏分数，本轮 GHOST 没有任何维度分 → 不会进 rows → 旧 `picked=True` 残留 → 报告里继续出现。

**修复**（`src/mo_stock/scorer/combine.py`）：

```python
# v2.3：DELETE 当日全部 + INSERT 本轮结果（每日一份完整快照）
session.execute(
    delete(SelectionResult).where(SelectionResult.trade_date == trade_date)
)
if rows:
    session.execute(insert(SelectionResult).values(rows))
```

- 顺带把 `pg_insert` 换成标准 `sqlalchemy.insert`（不再需要 `on_conflict_do_update`，且 SQLite 测试更友好）
- docstring 幂等语义同步更新

新增 2 个回归测试（`TestSelectionResultRefresh`）：
1. `test_stale_picked_does_not_survive_rerun`：模拟 GHOST.SH 旧 `picked=True` 残留 + 本轮无分 → 验证不再 picked，报告查询路径只返回 LIVE
2. `test_other_trade_dates_not_affected`：DELETE 只清当日，不误伤昨日数据

测试：367 → **369 passed**。

### 10.4 完整变更清单

| 文件 | 改动 |
|------|------|
| `src/mo_stock/filters/limit_filter.py` | 删除 `sector_heat_bonus` 逻辑 + 函数 + docstring |
| `src/mo_stock/scorer/combine.py` | 新增 `replace_filter_scores`；`combine_cfg` 参数；板块 cap + unknown cap + 空 map 警告；`selection_result` DELETE+INSERT |
| `src/mo_stock/storage/repo.py` | 删除 unused `get_l1_limit_count_map` |
| `src/mo_stock/cli.py` | 改用 `replace_filter_scores`；传 `combine_cfg` |
| `src/mo_stock/scheduler/daily_job.py` | 同上（生产路径对齐） |
| `config/weights.yaml` | `combine.max_stocks_per_sector: 4`、`max_unknown_sector_stocks: 0` |
| `tests/unit/test_limit_filter.py` | 移除 `TestSectorLimitHeatBonus` |
| `tests/integration/test_combine_sector_cap.py` | **新增** 9 个集成测试覆盖 cap、replace、selection refresh |

### 10.5 Residual（P1，不在本次范围）

- 同分 tiebreaker：`combine.py` 仍仅按 `final_score` 排序，平局取决于 dict 插入序
- 集中度指标统计（HHI / max_share / sector_count）落库或日志化
- moneyflow / theme / sector 分档逐步平滑化（z-score / 百分位）

---

**审计人**：Claude Code（Opus 4.7）
**复核**：Codex（三轮 CR：原审计 → 实施细节 → 数据残留两轮 HIGH）
**日期**：2026-04-28
**关联文档**：[`audit-2026-04-26.md`](audit-2026-04-26.md)、[`scoring.md`](scoring.md)
