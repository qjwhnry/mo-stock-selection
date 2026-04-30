# mo-stock 打分规则说明

> 当前版本对应代码：[src/mo_stock/filters/](../src/mo_stock/filters/)、[src/mo_stock/scorer/combine.py](../src/mo_stock/scorer/combine.py)、[config/weights.yaml](../config/weights.yaml)
> 文档生成：2026-04-25，最后更新：2026-04-29（v2.3：移除板块溢出 + 审计修复）

## ⚡ 关键设计原则

**每个维度独立 0-100 分，权重纯由 `dimension_weights` 决定**（不依赖各维度上限差异）。
保证 weights.yaml 配置的权重比例在综合分中真正生效。

## 1. 总览

每个交易日 `mo-stock run-once` 会跑完整选股流程：

```
ingest → 5 个 filter 各自打分 (0-100) → combine 加权融合 → 硬规则淘汰 → TOP 20 → 渲染报告
```

**6 个维度**（`config/weights.yaml` 配置权重，v2.1 总和 = 1.00 固定分母）：

| 维度 | 权重 | 数据源 | 实现状态 |
|------|------|--------|---------|
| `limit` 异动涨停 | 0.25 | `limit_list` 表 | ✓ [LimitFilter](../src/mo_stock/filters/short/limit_filter.py) |
| `moneyflow` 主力资金流向 | 0.25 | `moneyflow` + `daily_kline.amount` | ✓ [MoneyflowFilter](../src/mo_stock/filters/short/moneyflow_filter.py) |
| `lhb` 龙虎榜（base 60 + seat 40） | 0.20 | `lhb` + `lhb_seat_detail`（v2.1） | ✓ [LhbFilter](../src/mo_stock/filters/short/lhb_filter.py) |
| `sector` 申万一级行业 | 0.10 | `sw_daily` + `index_member` | ✓ [SectorFilter](../src/mo_stock/filters/short/sector_filter.py) |
| `theme` 同花顺概念 + 涨停最强 + 资金流（v2.1 新增） | 0.10 | `ths_daily` + `limit_concept_daily` + `ths_concept_moneyflow` | ✓ [ThemeFilter](../src/mo_stock/filters/short/theme_filter.py) |
| `sentiment` 新闻公告 | 0.10 | `news_raw` / `anns_raw` | ❌ 未实现 |

每个维度独立打分，0-100 分，**只对该维度有信号的股 append 结果**（score=0 视为信号缺失，由综合分公式按 0 处理）。

**v2.1 关键变化**：
- 把"题材增强"从 sector 维度拆出独立 `theme` 维度，避免维度饱和（多数强势股触顶 100）
- LhbFilter 重排为 base 60 + seat 40，机构席位净买给到 +20（远比 net_rate 信号更强）

---

## 2. 综合分公式（固定分母）

```
final_score = Σ(dim_score × weight) / Σ(全部权重)
            = Σ(dim_score × weight) / 1.00
```

**关键原则**：

- **分母固定为全部权重之和（= 1.00）**，缺失维度按 0 计入分子
- 严格惩罚单维霸榜，奖励多维共振
- 历史曾用过「动态分母」（只算 active_weight），但导致单维度极端分霸榜（违背多因子设计意图），2026-04-25 改为固定分母

**对照例**（同样的赤天化 600227 数据）：

| 公式 | final_score | 排名表现 |
|---|---|---|
| 旧版动态分母（已废弃） | 70 分 | 仅 lhb 一维度的可转债霸榜 TOP 1 |
| 新版固定分母（当前） | ~21 分 | 4 维齐全的强势股才能进 TOP |

代码位置：[scorer/combine.py:_weighted_combine](../src/mo_stock/scorer/combine.py)

---

## 3. limit 维度（异动涨停 + 断板反包）

### 关键设计：避开 hard_reject 干扰

**当日涨停股会被 `hard_reject.exclude_today_limit_up` 全部踢出 picked**。所以 LimitFilter 的真实产出是「跟涨停相关但今天非涨停」的断板反包股（PLAN.md 设计：「**首板 > 连板首日 > 断板反包**」）：

#### 3.1 当日涨停股（保留打分供 analyzer 单股查询）

**数据源**：`limit_list` 表（Tushare `limit_list_d`，仅 limit_type='U'）。

| 条件 | 加分 |
|---|---|
| 首板 / 2 连板 / N 连板（递减） | 20 / 30 / 30-(n-2)×5 |
| 封单 ≥ 1/5/10 亿 | 10 / 20 / 30 |
| 9:30-10:00 / 10-11 / 11-13:30 / 13:30+ | 15 / 10 / 5 / 0 |
| 每次开板 | -10 |
| 炸板 ≥ 2 次 | score 直接 0 |

> ⚠️ **当日涨停股最终都会被 hard_reject 踢出 picked**，这部分打分主要供 `mo-stock analyze 600519.SH` 单股查询使用。

#### 3.2 断板反包（核心产出）

**上一交易日涨停 + 今天没涨停 + 今天保持强势** = 断板反包股。这种股 hard_reject 不过滤。

> v2.3 修正：使用交易日历 `previous_trading_day` 查找上一交易日（而非自然日 -1），避免周一/长假后漏信号。

| 今天涨幅 | 加分 | 说明 |
|---|---|---|
| < 1% / 跌 | +0 | 不算反包（涨势没保住） |
| 1% - 3% | **+30** | 弱反包（基础分） |
| 3% - 5% | **+50** | 中反包 |
| 5% - 8% | **+70** | 强反包 |
| ≥ 8% | **+100** | 极强反包（满档） |

### 综合上限

- **断板反包**单维度最高 100（极强反包）

代码：[filters/short/limit_filter.py](../src/mo_stock/filters/short/limit_filter.py)
配置：`weights.yaml: limit_filter`

---

## 4. moneyflow 维度（主力资金流向）

**目标**：识别主力资金主动建仓的股票（不是被动挂单或散户接盘）。

**数据源**：`moneyflow`（Tushare `moneyflow`，主动主力口径）+ `daily_kline.amount`（用于算占比）。

### 单位说明

| 字段 | 单位 |
|---|---|
| `moneyflow.net_mf_amount` | **万元** |
| `moneyflow.buy_*_amount` / `sell_*_amount` | **万元** |
| `daily_kline.amount` | **千元**（Tushare daily 接口标准） |

### 打分公式

```
score = 0
+ today_bonus    （主力净流入 占当日成交 % 分档）
+ ratio_bonus    （大单+超大单净流入比例）
+ rolling_bonus  （近 3 日累计正）
- 散户接盘惩罚    （小单买 + 大单卖）
```

### 加分细则（占比分档，跨股可比）

| 主力净流入占成交比例（%） | 信号强度 | 加分 |
|---|---|---|
| ≥ 5% | 极强主动建仓 | **+50** |
| 1% - 5% | 强主动力度 | **+35** |
| 0.3% - 1% | 中主动力度 | **+20** |
| 0% - 0.3% | 弱信号（赤天化 0.157% 在此段） | **+5** |
| ≤ 0% / 数据缺失 | 净流出 | **不入榜（视为信号缺失）** |

> ⚠️ **关键设计**：跨股可比，避免大盘股偏弱、小盘股偏强。
> 公式：`ratio_pct = 1000 × net_mf_wan / daily_kline.amount_qy`（万元 / 千元单位换算）

| 大单+超大单 big_ratio | 加分 |
|---|---|
| < 0.4 | +0 |
| ≥ 0.4 | `min(30, big_ratio × 60)`（上限 +30） |

> `big_ratio = (buy_lg + buy_elg − sell_lg − sell_elg) / (∣buy_lg+buy_elg∣ + ∣sell_lg+sell_elg∣)`

| 近 3 日累计净流入 | 加分 |
|---|---|
| > 0 | **+20**（`rolling_3d_bonus`） |
| ≤ 0 | +0 |

### 扣分细则

| 条件 | 扣分 |
|---|---|
| 小单净买 > 0 **且** 大单净卖 < 0 | -30（`small_up_big_down_penalty`，散户接盘） |

### 实际上限

**100 分**：today +50 + ratio +30 + rolling +20 = 100。归一到 0-100。

### 注意：两套统计口径

Tushare `net_mf_amount` 是**主动主力**口径（外盘买、内盘卖），
跟「全量大单净额 = `(buy_lg+buy_elg) - (sell_lg+sell_elg)`」是**两套独立统计**。
我们 today_bonus 用前者（主动力度），ratio_bonus 用后者（全量大资金占比），
两者交叉验证。详见：[两种主力资金统计口径对比.md](./两种主力资金统计口径对比%20%26%20实战选用指南（详细版）.md)

代码：[filters/short/moneyflow_filter.py](../src/mo_stock/filters/short/moneyflow_filter.py)
配置：`weights.yaml: moneyflow_filter`

---

## 5. lhb 维度（龙虎榜）

**目标**：识别游资 / 机构席位主动净买入的强势股。

**数据源**：`lhb` 表（Tushare `top_list`），仅对当日上榜股打分。

### 单位说明

| 字段 | 单位 |
|---|---|
| `lhb.net_amount` / `l_buy` / `l_sell` / `l_amount` / `amount` | **元**（Tushare 文档说万元，实际是元，科学计数法 E7） |
| `lhb.net_rate` | **%**（Tushare 现成字段：`net_amount / amount × 100`） |
| `lhb.amount_rate` | **%**（Tushare 现成字段：`l_amount / amount × 100`） |

### 打分公式（v2.1：base 60 + seat 40 双层结构）

```
base = 20                        # 基础分（涨幅类上榜 + 净买入）
+ net_rate_tier_bonus            # 净买入占成交比例分档（0-20）
+ purity_bonus                   # 席位成交占成交比例分档（0-12）
+ reason_bonus                   # 上榜原因关键词（0-8）

seat = institution_buy_bonus     # 机构席位净买（0 or +20）
+ hot_money_buy_bonus            # 知名游资净买（0 or +12）
- hot_money_sell_penalty         # 知名游资净卖（0 or -15）
+ northbound_buy_bonus           # 北向净买（0 or +8）

score = clamp(base + seat, 0, 100)
```

**口径标记**：v2.1 起 `detail.lhb_formula_version = 2`，与历史 0-100 base 量纲不直接横比。

### 上榜资格门槛（不入榜条件）

任一命中即整股跳过 LhbFilter，不入 results：

| 条件 | 处理 |
|---|---|
| **跌幅榜上榜**（reason 含「跌幅偏离」「跌幅达」） | 跳过，避免「机构抄底跌停股」混入 |
| **net_rate ≤ 0 / NULL**（净卖出） | 跳过，视为信号缺失 |

### base 加分细则（v2.1 重排，上限 60）

| net_rate（净买入占成交 %） | 信号强度 | 加分 |
|---|---|---|
| ≥ 10% | 极强建仓 | **+20** |
| 5% - 10% | 强建仓 | **+15** |
| 2% - 5% | 中等（赤天化 600227 真实 3.36% 在此段） | **+10** |
| 0% - 2% | 太弱 | +0 |

| amount_rate（席位成交占当日成交 %） | 加分 |
|---|---|
| ≥ 30% | **+12**（席位主导今日成交，满档） |
| 15% - 30% | **+6**（赤天化真实 23.42% 在此段） |
| < 15% | +0 |

| 上榜原因关键词 | 加分 |
|---|---|
| 「连续三日涨幅」 | **+8**（趋势最强） |
| 「无价格涨跌幅限制」/「日涨幅偏离」/「日换手率」 | **+5** |
| 跌幅类（`_is_drop_rebound_reason`） | **整股跳过**（不在此表） |
| 多关键词命中 | 取最大值（不叠加） |

### seat 加分细则（v2.1 新增，上限 40 / 下限 -15）

席位身份在 ingest 阶段分类（`lhb_seat_detail.seat_type`）：
- `institution`: 名称含 "机构专用"
- `northbound`: 名称含 "沪股通专用" / "深股通专用"
- `hot_money`: 完全等于 `hm_list.orgs` 的某个营业部
- `other`: 其它（v2.1 删除了 quant_like，避免误报）

| 席位类型 | 触发条件 | 加分 |
|---|---|---|
| institution | 净买 ≥ 1000 万 | **+20**（机构信号远比 net_rate 强） |
| hot_money | 净买 ≥ 500 万 | **+12** |
| hot_money | 净卖 ≥ 1000 万 | **-15**（量化 / 一日游风险） |
| northbound | 净买 ≥ 3000 万 | **+8** |

### 实际上限

**100 分**：base 满档 60 + seat 满档 40 = 100，clamp 到 0-100。

代码：[filters/short/lhb_filter.py](../src/mo_stock/filters/short/lhb_filter.py)
配置：`weights.yaml: lhb_filter`（v2.1 全部参数化，可热调）

---

## 6. sector 维度（板块/行业）

**目标**：找到强势板块里的股票。「板块强 + 个股顺势」胜率高于「孤勇逆板块」。

**数据源**：
- `sw_daily`（申万板块当日 + 近 3 日 pct_change）
- `index_member`（股票 → 申万一级板块映射，月度刷新）

### 关键设计：用 index_member 白名单 filter

`sw_daily` 表里 `sw_code LIKE '801%'` 包含**一/二/三级板块共 180 个**（如 801056 能源金属是二级），
直接 TOP 5 会被二三级板块（涨幅波动大）污染。

**正确做法**：用 `index_member.l1_code` 实际的 31 个 distinct 一级行业代码做白名单 filter。

### 打分公式

```
score = 0
+ rank_bonus    # 所属板块当日涨幅 TOP N 排名加分
+ trend_bonus   # 板块近 3 日均涨幅
```

### 加分细则

| 当日板块涨幅排名 | 加分 |
|---|---|
| 第 1 名 | **+70** |
| 第 2 名 | **+60** |
| 第 3 名 | **+50** |
| 第 4 名 | **+40** |
| 第 5 名 | **+30** |
| TOP 5 之外 | +0 |

| 板块近 3 日均涨幅 | 加分 |
|---|---|
| ≥ 5% | **+30**（趋势性强，满档） |
| 2% - 5% | **+15** |
| < 2% | +0 |

### 实际上限

**100 分**：rank #1 (+70) + 3 日均涨 ≥5% (+30) = 100。归一到 0-100。

代码：[filters/short/sector_filter.py](../src/mo_stock/filters/short/sector_filter.py)
配置：`weights.yaml: sector_filter` （`top_n_sectors: 5` 可调）

---

## 6.5. theme 维度（同花顺概念 + 涨停最强 + 资金流）— v2.1 新增

**目标**：捕捉**短线题材轮动**——A 股短线热点（AI、机器人、固态电池等）多来自 THS 概念而非 SW 行业。

**数据源**：
- `ths_daily`（同花顺概念/行业指数日行情，1232 个板块）
- `limit_concept_daily`（涨停最强概念榜，每日 TOP 20）
- `ths_concept_moneyflow`（概念板块当日资金流向）
- `ths_member`（股票 → 概念多对多映射，慢变量）

### 关键设计：多概念股取最高加分（不累加）

A 股一只票常同时挂在 5-20 个概念里。如果加分，沾边股会天然占优——题材轮动信号被噪音稀释。

**v2.1 决策**：跨概念取 max，**同一概念内三类信号 sum**：

```python
score(stock) = max over concepts of:
    ths_rank_bonus(rank in ths_daily TOP N) +
    limit_concept_bonus(rank in limit_cpt_list) +
    moneyflow_bonus(net_amount > 0)
```

**渐进降级**：ths_daily 为空（接口故障 / 积分用尽）时仍跑 limit_concept + moneyflow 单点信号；只有三类全空才提前返回。

### 打分公式

```
score = clamp(min(best, max_theme_bonus), 0, 100)
```

### 加分细则

| 同花顺概念涨幅排名（top_n_themes=10） | 加分 |
|---|---|
| 第 1 名 | **+50** |
| 第 2 名 | **+42** |
| 第 3 名 | **+35** |
| 第 4 名 | **+28** |
| 第 5 名 | **+22** |
| 6-10 名 | **+12** |
| TOP 10 外 | +0 |

| 涨停最强概念排名（limit_cpt_list rank） | 加分 |
|---|---|
| 第 1 名 | **+50** |
| 第 2-3 名 | **+35** |
| 第 4-5 名 | **+22** |
| 第 6-10 名 | **+12** |
| 排名外 | +0 |

| 概念资金净流入（ths_concept_moneyflow.net_amount） | 加分 |
|---|---|
| > 0（亿元） | **+15** |
| ≤ 0 / NULL | +0 |

注：分档表查找策略是"找 ≥ rank 的最小 key"——rank=4 时若表里只有 1/2/3/5，返回 5 对应分数（保守）。

### 实际上限

**100 分**：ths rank 1 (+50) + limit rank 1 (+50) + moneyflow > 0 (+15) = 115，clamp 到 100。

代码：[filters/short/theme_filter.py](../src/mo_stock/filters/short/theme_filter.py)
配置：`weights.yaml: theme_filter`（top_n_themes / 三档分值 / max_theme_bonus 全部可热调）

---

## 7. sentiment 维度（新闻公告 / 研报情绪）

**当前未实现**，权重 0.10 在配置里，但 SentimentFilter 类不存在 → 综合分上限被这一维度拉低 10 分。

**计划**（待实现）：
- ingest `news_raw` / `anns_raw`（Tushare `news` / `anns_d`）
- 写 SentimentFilter，按公告关键词正负面打分
- 接入研报评级（`research_report` 接口）

详见 PLAN.md。

---

## 8. 硬规则（hard_reject）

综合分排序后，应用硬规则**淘汰**（不影响维度打分本身，只决定 picked 与否）。

| 规则 | 默认值 | 触发条件 |
|---|---|---|
| `exclude_st` | true | `stock_basic.is_st = True` 或股票名含 ST/*ST |
| `min_list_days` | 60 | 上市不足 60 天的次新股 |
| `exclude_today_limit_up` | true | 当日涨停（`limit_list.limit_type='U'`），避免次日追高 |
| `exclude_today_limit_down` | true | 当日跌停（`daily_kline.pct_chg ≤ -9.8`），避免次日抄底 |
| `negative_announcement_keywords` | 6 个关键词 | 近 7 日公告标题命中"立案调查""商誉减值"等 |

**注意**：`negative_announcement_keywords` 依赖 `anns_raw` 表，**当前 anns_raw 表为空**，此规则等同 noop。

代码：[scorer/combine.py:_build_hard_reject_map](../src/mo_stock/scorer/combine.py)
配置：`weights.yaml: hard_reject`

---

## 9. 综合分实例：赤天化 600227（2026-04-24 真实数据）

### 各维度得分

| 维度 | 触发条件 | 加分 | 总分 |
|---|---|---|---|
| **limit** | 不是涨停股 | +0 | **0** |
| **moneyflow** | net_mf=221 万 / 成交 14 亿 = 0.157% 占比 → +3 弱信号<br>big_ratio=0.157 < 0.4 → +0<br>3 日累计 17015 万 > 0 → +15 | +18 | **18** |
| **lhb** | 上榜（涨幅类）+30<br>net_rate=3.36% → +10<br>amount_rate=23.42% → +10<br>reason="日换手率达..." → +5 | +55 | **55** |
| **sector** | 食品饮料板块涨幅第 2 → +45<br>3 日均涨 < 2% → +0 | +45 | **45** |
| **theme** | 未命中概念排名 / 资金流 | +0 | **0** |
| **sentiment** | 未实现 | +0 | **0** |

### 综合分

```
final = (0×0.25 + 18×0.25 + 55×0.20 + 45×0.10 + 0×0.10 + 0×0.10) / 1.00
      = 0 + 4.5 + 11 + 4.5 + 0 + 0
      = 20.0 分
```

### 硬规则

| 规则 | 检查 | 结果 |
|---|---|---|
| `exclude_st` | 不是 ST | ✓ 通过 |
| `min_list_days` | 上市 > 60 天 | ✓ 通过 |
| `exclude_today_limit_up` | 当日涨幅 +7.61%，非涨停 | ✓ 通过 |
| `exclude_today_limit_down` | 不是跌停 | ✓ 通过 |
| 负面公告关键词 | anns_raw 表为空 | ✓ 通过（noop） |

→ **picked = True**，rank = 1（当日 TOP 1）

---

## 10. 综合分理论上限

**5 维全部归一到 100 之后**：

```
理想最强股（5 维全各 100 + sentiment 0）：
  = (100×0.25 + 100×0.25 + 100×0.20 + 100×0.10 + 100×0.10 + 0×0.10) / 1.00
  = 25 + 25 + 20 + 10 + 10 + 0
  = 90 分
```

**90 分是当前项目能算出来的最高分**（sentiment 维度未实现，永远 0，占 0.10 权重）。

历史对比：

| 时间 | 各维度上限 | 综合理论上限 |
|---|---|---|
| 2026-04-25 早期 | limit 100 / mf 65 / lhb 80 / sector 70 | **67.75** |
| v2.1（4 维归一后） | **全 100** | **85** |
| v2.1 起（5 维） | **全 100** | **90** |
| Phase 2 接 sentiment 后 | 全 100 | **100** |

### 实际更低的原因

1. **5 维全亮的股不存在**：要求同时满足 涨停 + 主力大额净流入 + 龙虎榜上榜 + 板块第 1 + 题材第 1 + 利好情绪 → 现实极少
2. **每个维度内部满分极难**：limit 100 要求"早封 + 大封单 + 2 连板"等多重条件
3. **sentiment 0.10 权重永远 0**：直接让上限降 10 分

### 想看更高分有 4 条路

| 思路 | 改动 | 上限提升 | 推荐度 |
|---|---|---|---|
| **A. 接入 sentiment** | 实现 `SentimentFilter` + 接入 news_raw / anns_raw | 67.75 → ~80 | ⭐⭐⭐⭐⭐ |
| **B. 改回动态分母** | 但会重新引发"单维霸榜" bug | 单维满分能跑 100 | ❌ 不要 |
| **C. 归一化显示** | 跑完后 `final / 67.75 × 100` 做视觉缩放 | 24 → 35 | ⭐⭐ 治标 |
| **D. 接受现状** | 看排名而非绝对分数 | 不变 | ⭐⭐⭐⭐ 实用 |

**选股系统的本质是排序**：TOP 1 = 当日 5400 只股里最稀缺的多维信号，**这才是有意义的判断**，不是分数绝对值。

---

## 11. 配置可热调（不改代码）

修改 [config/weights.yaml](../config/weights.yaml) 任意权重 / 阈值，重跑 `mo-stock run-once --skip-ingest` 即可重算（不重新拉数据）。

### 调权重示例

```yaml
# 把龙虎榜信号权重调高（原 0.20 → 0.30），其它相应调降
dimension_weights:
  limit: 0.20
  moneyflow: 0.20
  lhb: 0.30      # ← 提升
  sector: 0.10
  theme: 0.10
  sentiment: 0.10
```

### 调维度内细则

```yaml
moneyflow_filter:
  rolling_3d_bonus: 20      # 原 15，让趋势加成更重
  big_order_ratio_threshold: 0.3   # 原 0.4，让 ratio_bonus 更易触发
```

### 调硬规则

```yaml
hard_reject:
  exclude_today_limit_up: false   # 想看涨停股的综合分排名
  min_list_days: 30               # 放宽次新股门槛到 30 天
```

---

## 12. 调试技巧

### 单股拆解

```bash
mo-stock analyze 600227.SH --date 2026-04-24
```

输出该股 5 维度的详细 detail（各加分项、阈值命中情况），便于复盘"为什么没进 TOP 20"。

### SQL 查维度分布

```sql
-- 当日维度覆盖度（共振股有几只）
SELECT dim_count, count(*) AS stock_count FROM (
  SELECT ts_code, count(DISTINCT dim) AS dim_count
  FROM filter_score_daily
  WHERE trade_date = '2026-04-24'
  GROUP BY ts_code
) t GROUP BY dim_count ORDER BY dim_count DESC;

-- 共振股 + 综合分排名
SELECT s.rank, s.ts_code, b.name, s.rule_score,
       count(DISTINCT f.dim) AS dims, string_agg(f.dim, ',') AS dim_list
FROM selection_result s
JOIN filter_score_daily f USING (trade_date, ts_code)
JOIN stock_basic b USING (ts_code)
WHERE s.trade_date = '2026-04-24' AND s.picked
GROUP BY s.rank, s.ts_code, b.name, s.rule_score
ORDER BY s.rank;
```

---

## 13. AI 融合（v2.2 新增）

### 公式

```
final_score = rule_score × rule_weight + ai_score × ai_weight
            = rule_score × 0.6 + ai_score × 0.4   （weights.yaml 默认）
```

`combine_scores._final_score_from(rule, ai)` 实现：
- AI 缺失（`ai_score=None`）→ 直接返回 `rule_score`，保持 0-100 区间稳定
- 单股 AI 失败不阻断流程（其它股仍能拿 rule_score）

### 排序

v2.2 关键：算 `final_score` 后**重新按 final_score 排序**，再分配 `rank/picked`。
这样 AI 高分股能挤掉规则分高但 AI 看空的股票，AI 真正影响 TOP N。

v2.3 起，同分时使用多键 tiebreaker 保证排序确定性和业务解释性：

```
sort key = (-final_score, -rule_score, -active_dim_count, ts_code)
```

- `rule_score` 次之：规则分高的优先
- `active_dim_count`：有效维度数多者优先（多维共振优于单维极端）
- `ts_code` 兜底：保证同分股排序完全确定

### AI 输入范围

- 规则层算完 → 取未被硬规则淘汰的 TOP `settings.top_n_after_filter`（默认 50）
- 对每只调 `analyze_stock_with_ai`，结果落库 `ai_analysis`
- 50 之外的股票不调 AI（控制成本：每天 ~$0.5-1）

### 跳过 AI

`mo-stock run-once --skip-ai` → `combine_scores(enable_ai=False)` → ai_score 全为 None，行为等同 v2.1。

调试 / API 故障 / Tushare 限速时使用。

---

## 14. 历史变更

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-04-25 | 综合分公式动态分母 → 固定分母 | 4 维接通后单维霸榜（违背多因子设计） |
| 2026-04-25 | LhbFilter 跌幅榜上榜整股跳过 | 「机构抄底跌停股」跟"找强势股"目标矛盾 |
| 2026-04-25 | 硬规则加 `exclude_today_limit_down` | 跟 limit_up 对称，避免次日抄底 |
| 2026-04-25 | LhbFilter 净额从绝对金额改为占比（net_rate） | 跨股可比，避免大盘股偏弱、小盘股偏强 |
| 2026-04-25 | MoneyflowFilter today_bonus 占比分档 | 避免「净流入 221 万和 5 亿同一分」一刀切 |
| 2026-04-25 | **4 维度全部归一到 0-100** | 之前各维度上限不齐（65/70/80/100）让 weights.yaml 配置的权重失真 |
| 2026-04-25 | **LimitFilter 加断板反包 + 板块涨停溢出** | PLAN.md 设计落地：当日涨停股 hard_reject 干掉后，limit 维度真正能产出有效信号 |
| 2026-04-26 | **v2.1：拆 ThemeFilter + LhbFilter 重排（base 60+seat 40）** | 题材塞进 sector 会触顶 100；机构席位是远比 net_rate 强的信号 |
| 2026-04-26 | **v2.2：AI 层接入 Claude SDK + 4 段 prompt cache** | rule × 0.6 + ai × 0.4 融合，按 final_score 重排 TOP N，报告打印完整选出原因 |
| 2026-04-28 | **v2.3：移除板块涨停热度溢出 + 板块多样化 cap** | 与 sector 维度多重共线性导致单板块霸榜；cap 限制同板块最多 4 只 |
| 2026-04-29 | **v2.3 审计修复：断板反包改用交易日历、AI 成交额单位修正、tiebreaker** | 修复周一/长假漏信号、AI prompt 成交额偏差 1000 倍、同分排序不确定 |

---

**相关文档**：
- [docs/cli.md](./cli.md) - 命令行手册
- [docs/schema.md](./schema.md) - 数据库表结构
- [docs/两种主力资金统计口径对比.md](./两种主力资金统计口径对比%20%26%20实战选用指南（详细版）.md) - moneyflow 两套统计口径详解


---

## swing 策略评分规则（v2.4）

> 配置文件：`config/weights_swing.yaml`
> 详细设计：`docs/swing-strategy-plan-revised-2026-04-30.md`

### 综合分公式

与 short 策略共享同一公式框架：

    final_score = Σ(score_i × w_i) / Σ(全部权重之和 = 1.0)

### 7 维度权重

| 维度 | 权重 | 说明 |
|------|------|------|
| `trend` 趋势结构 | 0.27 | MA 多头排列 + 量价确认 |
| `pullback` 回踩承接 | 0.13 | 趋势内健康回撤 + 重新转强 |
| `moneyflow_swing` 波段资金 | 0.20 | 5/10 日资金持续性 |
| `sector_swing` 行业持续性 | 0.13 | 行业多日强度 + 派生资金聚合 |
| `theme_swing` 题材持续性 | 0.09 | 题材多日排名 + 资金确认 |
| `catalyst` 短线催化 | 0.08 | 断板反包 + 龙虎榜（低权重） |
| `risk_liquidity` 风险流动性 | 0.10 | 流动性、波动率、透支度质量分 |

### market_regime 组合层控制

`market_regime` 不进入单股权重，而是作为组合层控制：

- 数据源：沪深 300 MA20 趋势 + 全市场涨跌家数
- 根据分档动态调整 `top_n`、`position_scale`、`min_final_score`
- 大盘弱势时自动缩减候选数量和仓位

| 分档 | regime_score | top_n | position_scale |
|------|---:|---:|---:|
| 强势市 | ≥ 70 | 20 | 1.0 |
| 震荡市 | ≥ 50 | 15 | 0.7 |
| 弱势市 | ≥ 30 | 8 | 0.4 |
| 极弱市 | < 30 | 3 | 0.2 |

低于 `min_final_score=30` 的候选直接淘汰。

### ATR 自适应止损

    stop_pct = clamp(1.5 × ATR_pct(20), 4%, 10%)

ATR_pct = ATR_abs / close × 100，用百分比形式，clamp 限制在 [4%, 10%] 区间。
移动止盈：盈利 >10% 后，从高点回撤 2×ATR_pct 即退出。

### 维度打分规则

各维度满分 100，加分/扣分制。详细打分条件见 `docs/swing-strategy-plan-revised-2026-04-30.md` §3.2。
