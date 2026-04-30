# 波段策略落地计划（修订版 v5）

> 日期：2026-04-30
> 基于：Codex 初版方案 + 五轮审查（6 + 3 + 2 + 5 项修正）
> 定位：在现有 1-3 日短线选股系统之外，新增独立 `swing` 策略画像（5-20 交易日）

---

## 1. 核心决策

### 1.1 独立 profile，不改造短线

现有短线系统信号偏事件驱动（涨停、龙虎榜、单日资金），拉长持仓周期只会放大噪音。
波段策略复用数据采集和存储层，但评分维度、权重、报告和回测完全独立。

### 1.2 修订历史

| 版本 | 变更 |
|------|------|
| v1 | 初版方向：回测提前、market_regime、ATR 止损、量价确认、sector/theme 拆分 |
| **v2** | **修正 6 项工程问题**：market_regime 降仓机制、ATR 公式歧义、指数 K 线数据源、行业资金派生计算、持仓表区分回测/实盘、Alembic 路径 |
| **v3** | **修正 3 项隔离问题**：指数接口改用 `index_daily`、`filter_score_daily` 加 strategy 隔离维度、`ai_analysis` 加 strategy 防覆盖 |
| **v4** | **修正 2 项实施问题**：`market_regime` 移出单股权重、补充 Alembic 安全迁移顺序 |
| **v5** | **补充 5 项落地细节**：指数采集进 CORE、历史指数回填、字段映射验证、strategy 参数传递、指数缺失降级 |

---

## 2. 策略参数

### 2.1 持仓与风控

| 参数 | 值 | 说明 |
|------|---:|------|
| 预期持仓 | 5-20 交易日 | T+5 首次评估，T+10/T+20 退出窗口 |
| 单票目标收益 | 8%-15% | 分两档止盈 |
| 止损 | 见 §4.2 ATR 公式 | 自适应，不再用固定百分比 |
| 移动止盈 | 盈利 >10% 后，从高点回撤 2×ATR_pct(20) 退出 | 百分比形式 |
| 盈亏比要求 | ≥ 1.5:1 | |
| 同时持仓 | 5-8 只（大盘弱市自动缩减，见 §3.3） | |
| 单票仓位 | 10%-20% | 强信号不超过 20% |
| 板块集中度 | 同一申万一级最多 4 只 | 复用现有 `max_stocks_per_sector` |

### 2.2 目标股票画像

1. 中短期均线多头或刚扭转。
2. 股价在 MA20 附近，或突破后首次缩量回踩。
3. 近 5-10 日主力资金累计净流入为正。
4. 所属行业/题材处于 5-10 日持续强势（非单日脉冲）。
5. 量价配合：上涨放量、回踩缩量。
6. 大盘环境非单边下跌（market_regime 过滤）。
7. 非 ST、非次新、非停牌、无重大负面公告。

---

## 3. 评分框架

### 3.1 权重配置

新增 `config/weights_swing.yaml`，不修改现有 `config/weights.yaml`。

```yaml
strategy:
  name: swing
  holding_days: [5, 20]

dimension_weights:
  trend: 0.27              # 趋势结构 + 量价确认
  pullback: 0.13            # 回踩承接
  moneyflow_swing: 0.20     # 波段资金（5/10 日窗口）
  sector_swing: 0.13        # 行业多日持续性
  theme_swing: 0.09         # 题材多日持续性
  catalyst: 0.08            # 短线催化（limit/lhb 降权）
  risk_liquidity: 0.10      # 风险与流动性

# 总和 = 1.00。market_regime 不进入单股 rule_score，只做组合层仓位控制。

# ---- market_regime 降仓机制（v2 新增，v4 明确为组合层控制）----
# market_regime 不是普通打分维度，它还控制整体仓位和候选数量。
# 机制见 §3.3
market_regime_control:
  # regime_score 由 MarketRegimeFilter 独立计算（0-100），不写入单股维度分。
  # 不同分档决定 top_n 和仓位系数
  tiers:
    - min_score: 70    # 强势市
      top_n: 20
      position_scale: 1.0
    - min_score: 50    # 震荡市
      top_n: 15
      position_scale: 0.7
    - min_score: 30    # 弱势市
      top_n: 8
      position_scale: 0.4
    - min_score: 0     # 极弱市
      top_n: 3
      position_scale: 0.2
  # 低于此阈值的 final_score 不入选，即使 top_n 没满
  min_final_score: 30.0

# ---- ATR 止损参数（v2 新增）----
stop_loss:
  atr_period: 20
  atr_multiplier: 1.5
  min_stop_pct: 0.04     # 止损不低于 -4%
  max_stop_pct: 0.10     # 止损不超过 -10%

# ---- 持仓上限 ----
max_positions: 8
position_per_stock_pct: 0.15   # 单票默认 15%
```

### 3.2 维度打分规则

除 `market_regime` 外，每个单股维度满分 100，采用加分/扣分制。以下阈值为初始值，
**需回测校准**。`market_regime` 是全局环境分，只用于组合层控制。

#### 3.2.1 trend：趋势结构 + 量价确认（权重 0.27）

| 条件 | 加分 |
|------|---:|
| 收盘价 > MA20 | +15 |
| MA5 > MA10 > MA20（多头排列） | +20 |
| MA20 斜率为正（5 日线性回归斜率） | +15 |
| 收盘价 > MA60 或 MA20 上穿 MA60 | +10 |
| 近 20 日涨幅在 5%-35% | +15 |
| 近 5 日量比趋势向上（均量递增） | +10 |
| 突破日放量 > 1.5 倍 20 日均量 | +10 |
| 回踩日缩量 < 0.7 倍上涨阶段均量 | +5 |
| 近 20 日涨幅 > 60%（严重透支） | -30 |
| 近 5 日放量滞涨（量增价平或价跌） | -20 |

#### 3.2.2 pullback：回踩承接（权重 0.13）

| 条件 | 加分 |
|------|---:|
| 近 5 日最大回撤 3%-12% | +25 |
| 收盘价距 MA10/MA20 不超过 5% | +25 |
| 回撤期间成交量 < 上涨阶段均量 | +20 |
| 今日收盘重新站上 MA5 或 MA10 | +20 |
| 今日长上影且放巨量（> 2 倍均量） | -30 |

#### 3.2.3 moneyflow_swing：波段资金（权重 0.20）

| 条件 | 加分 |
|------|---:|
| 近 5 日主力累计净流入 > 0 | +25 |
| 近 10 日主力累计净流入 > 0 | +25 |
| 近 5 日净流入天数 ≥ 3 | +20 |
| 大单/超大单净流入占比稳定为正 | +20 |
| 小单净买 + 大单净卖（散户接盘） | -30 |

> 复用 `MoneyflowFilter` 逻辑，窗口从 3 日扩展到 5/10 日。

#### 3.2.4 sector_swing：行业多日持续性（权重 0.13）

| 条件 | 加分 |
|------|---:|
| 所属申万一级行业近 5 日涨幅 TOP 5 | +30 |
| 所属申万一级行业近 10 日涨幅 TOP 8 | +25 |
| 行业成分股近 5 日资金**净流入合计为正** | +25 |
| 行业涨幅 5 日内无单日大幅回落（日回撤 < 3%） | +20 |

> **行业资金净流入是派生计算**（v2 修正）：
> 现有个股 `moneyflow` 表，但无申万行业资金表。
> 实现方式：通过 `index_member` 获取行业成分股列表，聚合成分股的 `moneyflow.net_mf_amount`
> 得到行业级别资金流。这是一个按日缓存的重计算（31 个行业 × 5000 成分股），
> 建议在 `sector_swing_filter` 内部做一天粒度的缓存，避免重复聚合。
>
> 数据流：`index_member`(ts_code→l1_code) → join `moneyflow`(ts_code) → group by l1_code → sum

#### 3.2.5 theme_swing：题材多日持续性（权重 0.09）

| 条件 | 加分 |
|------|---:|
| 题材近 5 日排名 TOP 10 | +30 |
| 题材资金净流入为正 | +25 |
| 题材 + 行业同时命中 | +25 |
| 题材近 5 日排名持续上升 | +20 |

#### 3.2.6 catalyst：短线催化（权重 0.08）

| 来源 | 处理 |
|------|------|
| 断板反包 | +20（加分但不单独入选） |
| 龙虎榜机构净买 | +25 |
| 龙虎榜游资净买 | +10 |
| 当日涨停 | 不加分，仅作板块强度信号参考 |

#### 3.2.7 risk_liquidity：风险与流动性（权重 0.10）

| 条件 | 处理 |
|------|------|
| 20 日均成交额 < 5000 万 | -30 |
| 近 20 日振幅 > 40% | -25 |
| 近 3 日涨幅 > 15% 且放量 | -30 |
| 当前价距 MA20 > 20% | -25 |
| ST / 次新 / 停牌 / 负面公告 | 硬淘汰（复用现有 hard_reject） |

#### 3.2.8 market_regime：大盘环境评分（组合层控制，不进入单股权重）— v4 修正

**评分规则**：用于计算全市场统一的 `regime_score`，只控制 `top_n`、`position_scale` 和
`min_final_score`，不写入 `filter_score_daily`，也不参与单股 `rule_score`。

| 条件 | 加分 |
|------|---:|
| 沪深 300 收盘价 > MA20 | +20 |
| 沪深 300 MA20 斜率为正 | +20 |
| 全市场上涨家数占比 > 55% | +20 |
| 沪深 300 近 10 日涨幅 > 0 | +20 |
| 沪深 300 近 10 日涨幅 < -5% | -40 |
| 全市场上涨家数占比 < 35% | -30 |
| 沪深 300 MA20 下穿 MA60 | -30 |

> **数据来源（v2 修正）**：
> 当前 `DailyKline` 表和 `TushareClient.daily()` 接口拉取的是**个股**日线，
> **不包含 000300.SH 等指数 K 线**。实现 market_regime 有两个方案：
>
> **方案 A（推荐）**：新增 `index_daily` 数据采集。
> 在 `TushareClient` 新增 `index_daily()` 方法（Tushare 专用指数日线接口 doc_id=95），
> 再由 `DailyIngestor.ingest_index_daily(trade_date)` 调用并写入 `daily_kline` 表。
> 注意：虽然 `daily()` 接口也能查指数（通用行情接口），但 `index_daily` 字段更规范
> （含 turnover 等指数专有字段），且避免与个股查询混用同一接口增加限流压力。
> 优点：复用 `daily_kline` 表结构，MA/ATR 计算代码完全共享。
>
> **方案 B**：用 `sw_daily` 的全市场指数代理。
> 取所有申万一级行业指数涨幅的均值/中位数作为大盘代理。
> 优点：不需要新数据采集。缺点：不如沪深 300 直观。
>
> 建议选方案 A，在 Phase 0 一并实现，新增 ingestion 只需约 10 行代码。
>
> 涨跌家数统计：从 `daily_kline` 查 `trade_date = T` 的全 A 股记录，
> `pct_chg > 0` 计数 / 总计数，不需要新表。
>
> **缺失降级（v5 新增）**：如果 `000300.SH` 或 `000001.SH` 指数日线缺失、
> `index_daily` 超时、或涨跌家数统计异常，`market_regime_filter` 不应中断选股流程。
> 应记录 warning，并返回中性 `regime_score = 50`（震荡市默认值）。

### 3.3 market_regime 降仓机制（v2 新增，v4 修正）

> **为什么不能只靠打分维度实现降仓**（v2 修正）：
>
> 当前 `combine.py` 的 `_weighted_combine` 对缺失维度按 0 计入，且 `FilterScoreDaily`
> 只存储 `score > 0` 的记录（见 `combine.py:218`）。如果 market_regime 给所有股票
> 统一低分（比如 30 分），排名后仍然会取满 TOP N，只是整体分数低了。
> 用户看到"今天选了 20 只，分数都很低"，并不会自动减少候选数量。
>
> 因此需要**独立于单股评分之外的仓位控制层**。v4 起，`market_regime` 不再放入
> `dimension_weights`，避免同一个全局信号既影响每只股票分数、又影响组合仓位，造成语义重复。

机制设计：

```python
# combine_scores 中新增的逻辑（伪代码）
# market_regime 是全市场统一信号，返回一个标量，而不是每只股票一个 ScoreResult。
regime_score_val = market_regime_filter.score_market(session, trade_date) or 50

# 根据分档决定 top_n 和仓位系数
tier = get_regime_tier(regime_score_val, tiers_config)
effective_top_n = tier["top_n"]
position_scale = tier["position_scale"]

# 排名后应用 min_final_score 过滤
scored = [s for s in scored if s["final_score"] >= min_final_score]
# 再取 effective_top_n
picked = scored[:effective_top_n]
```

效果：
- 强势市（regime ≥ 70）：选 20 只，满仓操作。
- 震荡市（50-70）：选 15 只，仓位缩到 70%。
- 弱势市（30-50）：选 8 只，仓位缩到 40%。
- 极弱市（< 30）：选 3 只，仓位缩到 20%，几乎空仓。
- `min_final_score=30`：任何 final_score < 30 的候选直接淘汰，即使 top_n 没满。

---

## 4. 止损止盈机制（ATR 自适应）— v2 修正

### 4.1 ATR 计算

```python
# daily_kline 已有 OHLC，ATR 直接计算
TR = max(H - L, abs(H - prev_C), abs(L - prev_C))
ATR_abs(20) = mean(TR, 20)                              # 绝对值（元）
ATR_pct(20) = ATR_abs(20) / close * 100                  # 百分比形式
```

### 4.2 止损规则（v2 修正：消除公式歧义）

> **v1 问题**：`max(-6%, -1.5×ATR(20))` 有歧义——ATR 可以是绝对值也可以是百分比，
> 且 `max` 语义不明确（取绝对值更大的 = 止损更宽？还是更窄？）。
>
> **v2 修正**：统一用百分比形式，用 `clamp` 限制范围。

```python
def compute_stop_loss_pct(atr_pct_20: float, config: dict) -> float:
    """计算止损百分比（负数）。

    Args:
        atr_pct_20: ATR 占收盘价的百分比，如 3.5 表示 ATR = 3.5% 的价格
        config: weights_swing.yaml 的 stop_loss 配置段

    Returns:
        止损百分比，如 -6.0 表示止损 -6%
    """
    multiplier = config.get("atr_multiplier", 1.5)
    min_pct = config.get("min_stop_pct", 0.04)      # 4%
    max_pct = config.get("max_stop_pct", 0.10)      # 10%

    # 原始 ATR 止损 = 1.5 × ATR_pct
    raw_stop = multiplier * (atr_pct_20 / 100)

    # clamp 到 [4%, 10%]，确保不会太窄也不会太宽
    clamped = max(min_pct, min(raw_stop, max_pct))

    return -clamped * 100  # 返回负数，如 -6.0

# 示例：
# 低波动票 ATR_pct=2%  → stop = -clamp(3%, 4%, 10%) = -4%
# 中波动票 ATR_pct=4%  → stop = -clamp(6%, 4%, 10%) = -6%
# 高波动票 ATR_pct=8%  → stop = -clamp(12%, 4%, 10%) = -10%
```

止损价换算：

```python
stop_price = entry_price * (1 + compute_stop_loss_pct(atr_pct_20, config) / 100)
```

### 4.3 止损规则汇总

| 类型 | 规则 | 说明 |
|------|------|------|
| ATR 止损 | `entry_price × (1 - clamp(1.5 × ATR_pct, 4%, 10%))` | 自适应波动率 |
| 结构止损 | 有效跌破 MA20 且次日未收回 | 趋势破坏即退出 |
| 时间止损 | T+5 未盈利且 trend 维度转弱 | 退出或降仓 |

### 4.4 止盈规则

| 阶段 | 规则 |
|------|------|
| 第一目标 | +8% ~ +10%，可减半仓 |
| 第二目标 | +15% ~ +20%，看板块强度决定是否保留 |
| 移动止盈 | 盈利 >10% 后，从最高点回撤 2×ATR_pct(20) 即退出 |

---

## 5. 数据层评估

### 5.1 数据源需求

| 需求 | 数据源 | 状态 | 处理 |
|------|--------|------|------|
| MA5/10/20/60 | `daily_kline` OHLC | ✅ 已有 | 直接计算 |
| ATR(20) | `daily_kline` OHLC | ✅ 已有 | 直接计算 |
| 量比/换手率 | `daily_basic` | ✅ 已有 | 直接读取 |
| 主力资金 5/10 日 | `moneyflow` | ✅ 已有 | 窗口扩展 |
| 行业 5/10 日涨幅 | `sw_daily` | ✅ 已有 | 窗口扩展 |
| 题材持续性 | `ths_daily` + `ths_concept_moneyflow` | ✅ 已有 | 多日统计 |
| 涨跌家数 | `daily_kline` 全 A 统计 | ✅ 已有 | 不需新表 |
| **沪深 300 K 线** | `daily_kline` | ⚠️ **需要新增采集** | Phase 0 新增 ingestion |
| **行业资金净流入** | `moneyflow` + `index_member` 聚合 | ⚠️ **派生计算** | `sector_swing_filter` 内部聚合 |

### 5.2 新增数据采集（Phase 0）

**沪深 300 / 上证指数日线入 daily_kline**：

```python
# TushareClient 新增方法
def index_daily(self, ts_code: str | None = None, trade_date: str | None = None,
                start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """指数日线行情（Tushare index_daily, doc_id=95）。"""
    return self._call(
        "index_daily",
        ts_code=ts_code, trade_date=trade_date,
        start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )

# DailyIngestor 新增方法
INDEX_CODES = ["000300.SH", "000001.SH"]  # 沪深300 + 上证指数

def ingest_index_daily(self, trade_date: date) -> int:
    """拉指数日线，存入 daily_kline（复用现有表结构）。"""
    rows = []
    for ts_code in INDEX_CODES:
        df = self.client.index_daily(ts_code=ts_code, trade_date=date_to_tushare(trade_date))
        rows.extend(_daily_kline_rows_from_df(df))
    return repo.upsert_daily_kline(session, rows)
```

需确认：
- `daily_kline` 的 `ts_code` 字段（String(12)）是否支持 `000300.SH` 格式 → 应该支持，与个股格式一致。
- 现有 filter 查询是否会误取指数数据 → `stock_basic` 表不含指数代码，现有 filter 通过 `stock_basic` 关联，指数数据不会被卷入选股流程。
- `ingest_index_daily` 必须加入 `DailyIngestor.ingest_one_day()` 的 `core_steps`，
  而不是 `enhanced_steps`。这样 `run-once`、`backfill`、`scheduler` 都会自动拉指数数据。
- 当前 `ingest_daily_kline()` 是行内构造 row dict。实现 `index_daily` 时应抽出
  `_daily_kline_rows_from_df(df)` 共用映射逻辑，并验证 `index_daily` 返回字段。
  `index_daily` 比 `daily` 可能多出 `change` 等字段，额外字段可忽略，但 OHLCV、
  `pre_close`、`pct_chg`、`vol`、`amount` 必须确认可映射。

历史指数补齐（v5 新增）：

- Phase 0 落地后，已有 180 天个股 K 线并不会自动拥有指数 K 线。
- 推荐新增轻量命令：

```bash
mo-stock refresh-index --days 180
```

- 该命令只调用 `ingest_index_daily()` 补指数，不重跑个股、资金、龙虎榜等昂贵接口。
- 如果暂时不加命令，也必须在 Phase 0 验收时对已有数据区间执行一次指数回填。

### 5.3 行业资金派生计算

`sector_swing_filter` 内部实现：

```python
def _sector_moneyflow(session, trade_date, l1_code, window=5):
    """聚合申万一级行业近 N 日资金净流入。

    数据流：index_member(ts_code→l1_code) → join moneyflow(ts_code) → sum
    """
    # 1. 获取行业成分股
    member_stmt = select(IndexMember.ts_code).where(IndexMember.l1_code == l1_code)
    members = session.execute(member_stmt).scalars().all()

    # 2. 获取近 N 日资金流（使用 trade_cal 确保 N 个交易日）
    trade_dates = get_recent_trade_dates(session, trade_date, window)
    mf_stmt = (
        select(Moneyflow.ts_code, func.sum(Moneyflow.net_mf_amount))
        .where(Moneyflow.ts_code.in_(members))
        .where(Moneyflow.trade_date.in_(trade_dates))
        .group_by(Moneyflow.ts_code)
    )
    # 3. 合计
    total = sum(row[1] for row in session.execute(mf_stmt).all())
    return total
```

性能考虑：31 个行业 × 5000 成分股，建议在 `sector_swing_filter.score_all` 中
一次性聚合所有行业的资金流，而非逐行业查询。可缓存到 dict 避免重复计算。

### 5.4 预处理要求

- MA 和 ATR 需要至少 60 日历史 K 线（当前保留 180 天，足够）。
- 5/10 日滚动窗口需要 `trade_cal` 确保使用交易日而非自然日（已有）。

---

## 6. 数据库变更

### 6.1 结果表增加 strategy 字段（Phase 0）

推荐在 `selection_result` 表增加 `strategy` 字段，而非新建独立表。

原因：
- 后续可能继续增加策略（position、low_volatility 等），独立表会导致重复代码。
- 唯一键从 `(trade_date, ts_code)` 调整为 `(trade_date, strategy, ts_code)`。

Migration 要点：

```python
# 新增字段
strategy: Mapped[str] = mapped_column(
    String(20), default="short",
    comment="策略标识：short / swing",
)

# 唯一键变更
UniqueConstraint("trade_date", "strategy", "ts_code", name="uq_selection_key")
```

同步修改 `filter_score_daily` 表：

```python
# 新增字段（与 SelectionResult.strategy 对齐）
strategy: Mapped[str] = mapped_column(
    String(20), default="short",
    comment="策略标识：short / swing",
)

# 唯一键变更：加入 strategy
UniqueConstraint("trade_date", "strategy", "ts_code", "dim", name="uq_filter_score_key")
```

> **为什么 `filter_score_daily` 也需要 strategy**（v3 新增）：
>
> 当前 `combine_scores`（`combine.py:203`）查询 `FilterScoreDaily` 时只按 `trade_date` 过滤：
> ```python
> stmt = select(FilterScoreDaily).where(FilterScoreDaily.trade_date == trade_date)
> ```
> 如果同一天跑了 short 和 swing 两种策略，所有维度分数都混在一起。
> 虽然 `_weighted_combine` 会忽略不在权重配置中的 dim（按 0 处理），
> 但这意味着 short 的 limit/lhb 分数和 swing 的 trend/pullback 分数
> 会同时出现在内存中，增加不必要的噪音和内存占用。
>
> 加 strategy 后，查询改为：
> ```python
> stmt = select(FilterScoreDaily).where(
>     FilterScoreDaily.trade_date == trade_date,
>     FilterScoreDaily.strategy == strategy,  # 新增过滤
> )
> ```
> 确保每个策略只读自己的维度分数，短线/波段重跑互不干扰。
>
> 同步要求：`persist_filter_scores()` / `replace_filter_scores()` 都要接收 `strategy`
> 参数（默认 `"short"`）。其中 `replace_filter_scores()` 删除旧分数时必须带上
> `trade_date + strategy + dim` 条件，避免重跑 swing 时误删 short 的分数，反之亦然。
>
> `combine_scores()` 也要新增 `strategy: str = "short"` 参数，并继续向下传递：
> - 读 `FilterScoreDaily` 时按 `strategy` 过滤。
> - 写 `SelectionResult` 时写入同一个 `strategy`。
> - 调 `analyze_stock_with_ai()` 时传入 `strategy`，让 AI 读写对应策略的 `ai_analysis`。
> - 报告渲染查询 `selection_result` / `ai_analysis` 时也要按 `strategy` 过滤。

同步修改 `ai_analysis` 表（v3 新增）：

```python
# 新增字段
strategy: Mapped[str] = mapped_column(
    String(20), default="short",
    comment="策略标识：short / swing",
)

# 唯一键变更
UniqueConstraint("trade_date", "strategy", "ts_code", name="uq_ai_analysis_key")
```

> **为什么 `ai_analysis` 需要 strategy**（v3 新增）：
>
> 当前唯一键是 `(trade_date, ts_code)`。如果同一天对同一只股同时跑 short AI 和 swing AI，
> 后写的会覆盖先写的（upsert 冲突）。加 strategy 后，short 和 swing 的 AI 分析独立存储。

Alembic 安全迁移顺序（v4 新增）：

1. 给 `selection_result`、`filter_score_daily`、`ai_analysis` 增加 `strategy` 字段，先允许
   nullable 或设置 server default `'short'`。
2. 回填历史数据：`UPDATE ... SET strategy = 'short' WHERE strategy IS NULL`。
3. 将 `strategy` 改为 NOT NULL，并保留默认值 `'short'`。
4. 删除旧唯一约束：`uq_selection_key`、`uq_filter_score_key`、`uq_ai_analysis_key`。
5. 创建新唯一约束：
   - `selection_result(trade_date, strategy, ts_code)`
   - `filter_score_daily(trade_date, strategy, ts_code, dim)`
   - `ai_analysis(trade_date, strategy, ts_code)`
6. 同步更新相关索引，至少保留 `(trade_date, strategy)` 或 `(trade_date, strategy, dim)` 查询索引。

这样历史短线数据会全部归属 `short`，不会因为新增非空字段或唯一键切换导致迁移失败。

### 6.2 持仓状态表（Phase 1）— v2 修正：区分回测/实盘

> **v1 问题**：回测和实盘共用 `swing_position` 表，回测数据会污染真实持仓状态。
>
> **v2 修正**：表中增加 `mode` 和 `backtest_run_id` 字段，回测和实盘隔离。

```python
class SwingPosition(Base):
    """波段策略持仓状态跟踪（回测 + 实盘共用，通过 mode 隔离）。"""
    __tablename__ = "swing_position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(
        String(20), index=True,
        comment="运行模式：backtest / live",
    )
    backtest_run_id: Mapped[str | None] = mapped_column(
        String(36), index=True,
        comment="回测批次 ID（mode=backtest 时填写，如 UUID）；live 时为 NULL",
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, comment="记录日期")
    ts_code: Mapped[str] = mapped_column(String(12), index=True, comment="股票代码")
    status: Mapped[str] = mapped_column(
        String(20), comment="持仓状态：watching / holding / stopped / exited"
    )
    entry_price: Mapped[float | None] = mapped_column(Float, comment="入场价")
    entry_date: Mapped[date | None] = mapped_column(Date, comment="入场日期")
    stop_loss_price: Mapped[float | None] = mapped_column(
        Float, comment="当前止损价；watching 状态可为空",
    )
    target_price: Mapped[float | None] = mapped_column(Float, comment="目标价")
    atr_at_entry: Mapped[float | None] = mapped_column(Float, comment="入场时 ATR(20)")
    max_price: Mapped[float | None] = mapped_column(Float, comment="持仓期最高价（移动止盈用）")
    pnl_pct: Mapped[float | None] = mapped_column(Float, comment="当前浮动盈亏 %")
    exit_reason: Mapped[str | None] = mapped_column(String(50), comment="退出原因")
    holding_days: Mapped[int | None] = mapped_column(Integer, comment="持仓交易日数")
    detail: Mapped[dict | None] = mapped_column(JSONB, comment="补充信息")

    __table_args__ = (
        Index("ix_swing_pos_mode_date", "mode", "trade_date"),
        Index("ix_swing_pos_run_id", "backtest_run_id"),
        {"comment": "波段持仓跟踪（回测/实盘通过 mode 隔离，回测用 backtest_run_id 批次管理）"},
    )
```

查询时始终带 `WHERE mode = 'backtest'` 或 `WHERE mode = 'live'`，确保隔离。
回测清理：`DELETE FROM swing_position WHERE mode='backtest' AND backtest_run_id = ?`

---

## 7. 工程落地计划

### Phase 0：策略配置 + DB Migration + 指数数据（预估 2-3 天）

| 任务 | 产出 |
|------|------|
| 新增 `config/weights_swing.yaml` | 波段独立权重 + regime 控制 + 止损参数 |
| `SelectionResult` / `FilterScoreDaily` / `AiAnalysis` 三表增加 `strategy` 字段 | Alembic migration |
| 唯一键调整：三表均改为 `(trade_date, strategy, ...)` | 防止策略间数据覆盖 |
| `persist_filter_scores` / `replace_filter_scores` 增加 `strategy` 参数 | 重跑时只替换当前策略分数 |
| `combine_scores(strategy="short")` 签名和查询增加 `strategy` | 只读/只写当前策略数据 |
| `analyze_stock_with_ai` / report 查询增加 `strategy` | 防止 short / swing AI 与报告互相覆盖 |
| CLI 增加 `--strategy` 参数 | `mo-stock run-once --strategy swing` |
| 加载权重时按 strategy 选择文件 | `load_weights_yaml` 扩展 |
| **新增 `TushareClient.index_daily()` 方法** | 指数日线专用接口（doc_id=95） |
| **新增 `DailyIngestor.ingest_index_daily()`** | 沪深 300 + 上证指数写入 `daily_kline` |
| `ingest_index_daily` 加入 `ingest_one_day.core_steps` | run-once / backfill / scheduler 自动拉指数 |
| 新增 `refresh-index --days 180` 或等价回填流程 | 补齐已有历史区间的指数 K 线 |
| `combine.py` 增加 market_regime 组合层控制 | `effective_top_n` + `min_final_score` + `position_scale` |

不改任何现有短线逻辑，`--strategy short`（默认）行为完全不变。

### Phase 1：波段规则层 MVP + 持仓状态表（预估 3-5 天）

新增文件：

| 文件 | 职责 |
|------|------|
| `src/mo_stock/filters/trend_filter.py` | MA 结构 + 量价确认 + 20 日涨幅评估 |
| `src/mo_stock/filters/pullback_filter.py` | 回踩 MA10/MA20 + 缩量回撤 + 重新转强 |
| `src/mo_stock/filters/moneyflow_swing_filter.py` | 5/10 日资金持续性 |
| `src/mo_stock/filters/sector_swing_filter.py` | 行业多日强度 + **派生资金聚合** + 回撤稳定性 |
| `src/mo_stock/filters/theme_swing_filter.py` | 题材多日持续性 |
| `src/mo_stock/filters/risk_liquidity_filter.py` | 流动性、波动率、透支度 |
| `src/mo_stock/filters/market_regime_filter.py` | 大盘环境判断（指数 MA + 涨跌家数），输出全局 regime_score |
| `src/mo_stock/storage/models.py` | 新增 `SwingPosition` ORM 模型 |

除 `market_regime_filter.py` 外，其它波段 filter 继承 `FilterBase`，输出 `ScoreResult`
（dim 字段使用 swing 维度标识）。`market_regime_filter.py` 输出全局 `regime_score`，
只给组合层控制使用，不落 `filter_score_daily`。

复用现有：
- `MoneyflowFilter` 的资金计算逻辑（扩展窗口）
- `SectorFilter` 的行业匹配逻辑（扩展窗口）
- `ThemeFilter` 的题材排名逻辑（多日聚合）
- `hard_reject` 全部复用

测试要求：

- MA 多头排列得分计算
- ATR(20) 计算准确性
- ATR 止损百分比 clamp 范围 [4%, 10%]
- 回撤到 MA20 后反弹得分
- 5/10 日资金累计为正得分
- 行业资金聚合（mock index_member + moneyflow）
- 极端高位透支扣分
- 大盘弱势时 market_regime 返回低分 + top_n 缩减
- 指数数据缺失时 market_regime 降级为中性 50，不中断主流程
- `ingest_one_day(skip_enhanced=True)` 仍会执行 `ingest_index_daily`
- 周末/节假日窗口使用交易日（非自然日）

### Phase 2：回测模块（预估 3-5 天）

| 文件 | 职责 |
|------|------|
| `src/mo_stock/backtest/__init__.py` | 模块入口 |
| `src/mo_stock/backtest/engine.py` | 回测引擎主逻辑 |
| `src/mo_stock/backtest/metrics.py` | 胜率、盈亏比、最大回撤、期望收益 |
| `src/mo_stock/backtest/runner.py` | CLI 入口 + 参数解析 |

回测流程：

```
1. 生成 backtest_run_id (UUID)
2. 遍历每个交易日 T：
   a. 调用 swing 全部 filter → 得到 swing_score
   b. 取 market_regime_tier → effective_top_n + position_scale
   c. 取 TOP N 候选（过滤 min_final_score）
   d. 按 position_scale 计算组合仓位，模拟入场（次日开盘价）
      → 写入 swing_position(mode='backtest', backtest_run_id)
   e. 跟踪持仓至 T+5/T+10/T+20，检查止损/止盈触发
   f. 更新 swing_position（status=stopped/exited, exit_reason, pnl_pct）
3. 汇总统计指标（按 backtest_run_id 查询）
```

回测输出指标：

| 指标 | 说明 |
|------|------|
| T+5 / T+10 / T+20 胜率 | 收益 > 0 的比例 |
| 平均盈亏比 | 平均盈利 / 平均亏损 |
| MFE / MAE | 最大有利涨幅 / 最大不利跌幅 |
| 止损触发率 | 触发止损的持仓占比 |
| 策略最大回撤 | 基于模拟组合净值曲线 |
| 行业集中度 | 单一行业平均占比 |
| 不同市场环境下的分段表现 | 按 market_regime 分档统计 |

CLI：

```bash
mo-stock backtest --strategy swing --start 2025-01-01 --end 2026-04-30 --top-n 20
```

### Phase 2.5：阈值校准（预估 2-3 天）

回测出结果后，根据数据调整：

1. 各维度加分/扣分阈值。
2. 权重比例。
3. ATR 止损倍数和 clamp 范围。
4. market_regime 分档阈值。
5. `min_final_score` 门槛。
6. TOP N 数量。

目标验收标准：

| 指标 | 初始目标 |
|------|---:|
| T+10 胜率 | ≥ 48% |
| 平均盈亏比 | ≥ 1.5 |
| 单票最大亏损 | 控制在 -8% 附近 |
| 策略最大回撤 | 观察 12%-18% 区间 |
| Top 20 板块集中度 | 同板块 ≤ 4 只 |

**如果回测结果不达标**：先调参，不急于进入 Phase 3。

### Phase 3：波段报告（预估 2-3 天）

输出到 `data/reports/swing/YYYY-MM-DD.md` 和 `.json`。

报告内容区别于短线日报：

| 区域 | 短线报告 | 波段报告 |
|------|---------|---------|
| 候选原因 | 事件 + 资金 | 趋势 + 回踩 + 资金 + 主线 |
| 入场条件 | 次日关注 | 回踩 MA10/MA20 后介入 |
| 止损 | 次日跌破 X | ATR 止损 + 结构止损 + 时间止损 |
| 目标位 | 无 | 第一目标 +8% / 第二目标 +15% |
| 持仓跟踪 | 无 | 已持仓股票状态更新 |
| 市场环境 | 无 | 大盘趋势判断 + 操作建议 + 仓位系数 |

### Phase 4：AI 波段增强（预估 2-3 天）

新增波段专用 prompt（`src/mo_stock/ai/prompts.py` 扩展）：

- 判断趋势可持续性（主线 vs 单日异动）。
- 输出结构止损条件（而非只给次日止损价）。
- 标记"适合观察、暂不适合买入"的候选。
- 基于市场环境给出仓位建议。

AI 不直接决定买卖，仍作为解释层和风险补充层。

---

## 8. 文件变更总览

### 新增文件

```
config/
  weights_swing.yaml                          # Phase 0

src/mo_stock/filters/
  trend_filter.py                             # Phase 1
  pullback_filter.py                          # Phase 1
  moneyflow_swing_filter.py                   # Phase 1
  sector_swing_filter.py                      # Phase 1
  theme_swing_filter.py                       # Phase 1
  risk_liquidity_filter.py                    # Phase 1
  market_regime_filter.py                     # Phase 1

src/mo_stock/backtest/
  __init__.py                                 # Phase 2
  engine.py                                   # Phase 2
  metrics.py                                  # Phase 2
  runner.py                                   # Phase 2

alembic/versions/
  xxxx_add_strategy_and_swing_position.py     # Phase 0

tests/
  unit/
    test_trend_filter.py                      # Phase 1
    test_pullback_filter.py                   # Phase 1
    test_moneyflow_swing_filter.py            # Phase 1
    test_sector_swing_filter.py               # Phase 1
    test_market_regime_filter.py              # Phase 1
    test_swing_backtest.py                    # Phase 2
```

### 修改文件

```
src/mo_stock/storage/models.py               # Phase 0: SelectionResult / FilterScoreDaily / AiAnalysis 三表 + strategy
                                                          Phase 1: SwingPosition 新增
src/mo_stock/scorer/combine.py               # Phase 0: strategy 参数路由 + regime 控制 + 维度查询按 strategy 过滤
src/mo_stock/filters/base.py                 # Phase 0: dim 标识注释扩展
src/mo_stock/data_sources/tushare_client.py  # Phase 0: 新增 index_daily() 方法
src/mo_stock/ingest/ingest_daily.py          # Phase 0: 新增 ingest_index_daily()
src/mo_stock/cli.py                          # Phase 0: 新增 refresh-index / strategy 参数透传
config/weights.yaml                           # 不改，short 策略保持原样
src/mo_stock/ai/prompts.py                   # Phase 4: 波段 prompt
```

---

## 9. 时间线

| Phase | 内容 | 预估 | 依赖 |
|-------|------|------|------|
| 0 | 配置 + DB Migration + 指数采集 | 2-3 天 | 无 |
| 1 | 规则层 MVP + 持仓表 | 3-5 天 | Phase 0 |
| 2 | 回测模块 | 3-5 天 | Phase 1 |
| 2.5 | 阈值校准 | 2-3 天 | Phase 2 |
| 3 | 波段报告 | 2-3 天 | Phase 2.5 达标后 |
| 4 | AI 波段增强 | 2-3 天 | Phase 3 |
| **总计** | | **16-22 天** | |

关键里程碑：**Phase 2.5 结束时，回测指标达标才能继续 Phase 3**。
如果不达标，回到 Phase 1 调参迭代。

---

## 10. MVP 验收标准

1. `mo-stock run-once --strategy short` 行为与当前完全一致，无任何回归。
2. `mo-stock run-once --strategy swing` 输出独立波段候选。
3. 每只候选至少有 3 类有效证据（趋势 + 资金 + 行业/题材）。
4. 报告中有入场条件、ATR 止损（clamp 4%-10%）、观察周期。
5. 回测 180 天：T+10 胜率 ≥ 48%、盈亏比 ≥ 1.5。
6. 大盘弱市时 effective_top_n 自动缩减、候选数量显著减少（regime 控制验证）。
7. 回测数据写入 `swing_position(mode='backtest')`，不影响实盘。
8. 行业资金流通过 `index_member` + `moneyflow` 聚合计算正确。
9. 已有历史区间完成指数 K 线补齐，`market_regime` 不因指数缺失长期降级为 50。
10. 单元测试覆盖所有新 filter + ATR 计算 + regime 降仓 + 回测引擎。

---

## 附录 A：审查修正对照

### 二轮审查（v2 修正，6 项）

| # | 问题 | 修正位置 | 修正内容 |
|---|------|---------|---------|
| 1 | market_regime 不能通过打分实现降仓 | §3.3 | 新增 `market_regime_control` 配置 + `effective_top_n` + `min_final_score` + `position_scale` |
| 2 | ATR 止损公式歧义 | §4.2 | 改为 `clamp(1.5 × ATR_pct, 4%, 10%)`，给出完整 Python 函数和示例 |
| 3 | 沪深 300 K 线不在 daily_kline | §3.2.8, §5.2 | 新增 `ingest_index_daily()`，Phase 0 实现 |
| 4 | 行业资金净流入不是现成字段 | §3.2.4, §5.3 | 明确标注"派生计算"，给出聚合伪代码和性能建议 |
| 5 | 回测持仓需区分模式 | §6.2 | SwingPosition 增加 `mode` + `backtest_run_id` 字段 |
| 6 | Alembic 路径错误 | §8 | 修正为 `alembic/versions/` |

### 三轮审查（v3 修正，3 项）

| # | 问题 | 修正位置 | 修正内容 |
|---|------|---------|---------|
| 7 | 指数日线应用 `index_daily` 接口而非 `daily` | §5.2 | 新增 `TushareClient.index_daily()`（doc_id=95），`daily` 是个股通用接口 |
| 8 | `combine_scores` 需按策略过滤维度 | §6.1 | `FilterScoreDaily` 加 `strategy` 字段，查询增加 `WHERE strategy = ?` 过滤 |
| 9 | `ai_analysis` 需策略隔离防覆盖 | §6.1 | `AiAnalysis` 加 `strategy` 字段，唯一键改为 `(trade_date, strategy, ts_code)` |

### 四轮审查（v4 修正，2 项）

| # | 问题 | 修正位置 | 处理 |
|---|------|----------|------|
| 10 | `market_regime` 同时在权重和组合层出现，语义重复 | §3.1 / §3.2.8 / §3.3 | 移出 `dimension_weights`，只作为组合层 `top_n` / `position_scale` / `min_final_score` 控制 |
| 11 | strategy 字段迁移需避免历史数据冲突 | §6.1 | 补充 Alembic 安全迁移顺序：先加字段和默认值、回填，再切唯一键 |

### 五轮审查（v5 修正，5 项）

| # | 问题 | 修正位置 | 处理 |
|---|------|----------|------|
| 12 | `ingest_index_daily` 若不加入 CORE，scheduler/backfill 不会自动拉指数 | §5.2 / §7 | 加入 `ingest_one_day.core_steps` |
| 13 | 既有 180 天历史缺指数 K 线 | §5.2 / §7 / §10 | 新增 `refresh-index --days 180` 或 Phase 0 指数回填验收 |
| 14 | `index_daily` 字段与 `daily` 可能不完全一致 | §5.2 | 抽 `_daily_kline_rows_from_df` 并验证字段映射 |
| 15 | `strategy` 需要贯穿 combine / persist / AI / report | §6.1 / §7 | `combine_scores(strategy="short")` 并向下传递 |
| 16 | 指数数据缺失不应中断选股 | §3.2.8 / §7 | `market_regime_filter` 异常或缺数时返回中性 50 |
