# 短线策略回测方案

> 日期：2026-05-07
> 策略：short（1-3 交易日短线）
> 状态：待实施
> 修订：v5 — 补跌停保守口径落库、停牌顺延精确化、数据预检、sector_l1 统一为独立列

## 1. 目标

对短线选股系统做**历史回测验证**（**复刻 `run-once --skip-ai` 规则层口径，不含 AI 层加成**），回答以下问题：

1. 选出来的股票，之后 1/2/3/5 日实际收益如何？
2. 综合分高的股票是否真的比低分股表现更好？（阈值校准）
3. 多维共振（3+ 维度命中）的胜率是否显著高于单维度？
4. 当前固定 5% 止损是否合理？

> **范围说明**：实际选股 `final_score = rule × 0.6 + ai × 0.4`，本回测仅验证 rule_score 排序有效性。
> AI 层回测需接入 Claude API，成本和不确定性较高，作为独立 Phase 后续考虑。

## 2. 方案选型

**方案 C：重算打分 + 缓存数据**

- 不重新调 Tushare API（用已回填的原始数据）
- 每个回测日重新跑 5 个 ShortFilter → combine → TOP N
- 再模拟 T+1 买入、多持有期收益

**为什么不用方案 A（完整回放）**：不需要重新 ingest，原始数据已在 daily_kline / limit_list / moneyflow 等表中。

**为什么不用方案 B（基于 selection_result）**：selection_result 只在 run-once 运行过的日期才有数据，历史日期稀疏，无法回测任意区间。

## 3. 回测流程

```
对回测区间 [start, end] 每个交易日 T：

  ① 重算打分（复刻 run-once --skip-ai 规则层口径）
     ├─ LimitFilter.score_all(session, T)
     ├─ MoneyflowFilter.score_all(session, T)
     ├─ LhbFilter.score_all(session, T)
     ├─ SectorFilter.score_all(session, T)
     └─ ThemeFilter.score_all(session, T)
     → _weighted_combine(dim_scores, weights) → rule_score
     → _build_hard_reject_map() → 过滤
     → 排序 tie-breaker: (-rule_score, -active_dim_count, ts_code)
     → 板块多样化 cap（max_stocks_per_sector）→ 最终 TOP N
     → 快照 sector_l1 到独立列（避免后续 index_member 更新漂移）

  ② 模拟交易
     ├─ 买入日 entry_date = T+1（下一个交易日）
     ├─ 买入价 entry_price = T+1 开盘价 (daily_kline.open)
     ├─ 涨停高开跳过：T+1 open 相对 T close 涨幅 > 板块涨跌幅限制则跳过
     └─ 数据缺失（T+1 无行情）：跳过该笔

  ③ 计算多持有期收益（每个持有期写一行）
     对每个 holding_days ∈ {1, 2, 3, 5}：
     ├─ 取 entry_date 起的 holding_days 个交易日的 K 线
     ├─ entry_date 即持有第 1 天
     ├─ exit_date / exit_price = 实际退出日和退出价（含止损修正）
     ├─ raw_return = 不止损的自然持有收益
     ├─ realized_return = 执行止损后的收益
     ├─ net_raw_return / net_realized_return = 扣费后收益
     ├─ max_return / max_drawdown = 持有期内极值（相对 entry_price）
     ├─ 跌停流动性标记（见 §4.1.1）
     └─ 停牌顺延处理（见 §4.3）

  ④ 写入 short_backtest_trade 表（每个信号 × 每持有期 = 一行）
```

### 3.1 持有期定义（修正 off-by-one）

**买入日 entry_date 就是持有第 1 天**：

| 持有期 | 含义 | 收盘价取值 |
|--------|------|-----------|
| 1d | 买入当天收盘卖出 | close[entry_date] |
| 2d | 买入后第 2 个交易日收盘卖出 | close[entry_date 后第 2 个交易日] |
| 3d | 买入后第 3 个交易日收盘卖出 | close[entry_date 后第 3 个交易日] |
| 5d | 买入后第 5 个交易日收盘卖出 | close[entry_date 后第 5 个交易日] |

### 3.2 涨停高开跳过阈值

与 swing 回测一致，按板块涨跌幅限制动态判断：

| 板块 | 阈值 |
|------|------|
| 主板（60/00 开头） | 9.5% |
| 创业板（300/301） | 19.5% |
| 科创板（688/689） | 19.5% |
| 北交所（BJ） | 29.5% |

### 3.3 复刻 run-once --skip-ai 排名逻辑

回测排名必须与 `combine_scores(enable_ai=False)` 一致（combine.py:162-391）：

1. `_weighted_combine()` — 固定分母加权融合
2. `_build_hard_reject_map()` — 硬规则淘汰
3. **排序 tie-breaker**（combine.py:298-305）：
   ```python
   # enable_ai=False 时 final_score == rule_score，简化为：
   scored.sort(key=lambda x: (-x["rule_score"], -x["active_dim_count"], x["ts_code"]))
   ```
4. **板块多样化 cap**（combine.py:324-391，当前内联在 combine_scores 中）

**实施要求**：Phase 0 需先将板块 cap 逻辑从 `combine_scores` 提取为独立函数
`apply_sector_cap(session, scored, max_per_sector=4, max_unknown=0)`，同时供 live 和 backtest 复用。
参数 `max_unknown` 对应 `max_unknown_sector_stocks`（无行业映射股票的入选上限，默认 0=不限）。
不提取的话，需在 `short_engine.py` 中独立实现一份等价逻辑（依赖 `repo.get_index_member_l1_map`）。

### 3.4 板块归属快照

`index_member` 按月刷新，历史回测时板块归属可能与信号日不同。
每笔记录在写入时快照 `sector_l1`（申万一级行业名称）到独立列 `sector_l1`，
后续按板块分组统计时使用快照值而非重新查询 index_member。如需行业代码等扩展信息可存入 `detail`。

## 4. 买入/卖出/止损规则

### 4.1 止损双轨收益（核心设计）

每条记录同时维护两套收益（扣费前 + 扣费后各一套，共 4 个收益字段）：

| 字段 | 含义 | 用途 |
|------|------|------|
| `raw_return_pct` | 不止损的自然持有收益 | 分析"止损是否合理" |
| `realized_return_pct` | 执行止损后的真实收益 | **默认指标计算用这个** |
| `net_raw_return_pct` | 扣费后的自然持有收益 | 分析扣费影响 |
| `net_realized_return_pct` | 扣费后 + 执行止损的真实收益 | **扣费后指标用这个** |

**止损判定**：

```
止损价 = entry_price × (1 - stop_loss_pct / 100)

对 holding_days 内每个交易日 d（从 entry_date 开始，1-based）：
  if daily_kline.low ≤ 止损价：
    stop_hit = True
    stop_day = d
    exit_date = d 对应的交易日
    exit_price = 止损价
    realized_return_pct = (止损价 - entry_price) / entry_price × 100
    break
  else：
    exit_date = entry_date 后第 holding_days 个交易日
    exit_price = close[exit_date]
    realized_return_pct = (exit_price - entry_price) / entry_price × 100
```

**报告口径**：
- 胜率、平均收益、盈亏比、最大亏损 → 用 `realized_return_pct`
- 扣费后指标 → 用 `net_realized_return_pct`
- 止损分析"止损后是否反弹" → 对比 `raw_return_pct` 和 `realized_return_pct`

#### 4.1.1 跌停流动性风险

止损触发日如果股票跌停（`pct_chg ≤ -9.5%`，或创业板/科创板 `≤ -19.5%`），实际**无法以止损价成交**，
因为跌停封死时流动性为零。此时 `realized_return_pct` 假设可成交会**高估**真实收益。

**处理方式**：

```
if stop_hit and 当日 pct_chg ≤ 板块跌停阈值 and close == low：
    detail.limit_down_locked = True
    # 保守估计：无法成交，顺延到下一交易日可成交价
    # 不声称 "以 low 成交"——只把接近跌停且收盘等于最低价视为流动性受限
    detail.liquidity_note = "止损日接近跌停且收盘等于最低价，保守视为流动性受限"
    # 落库保守估计字段（exit_date/exit_price/realized_return_pct 保留"假设止损价成交"口径）
    detail.liquidity_exit_date = 下一可交易日
    detail.liquidity_exit_price = 下一可交易日收盘价
    detail.liquidity_return_pct = (liquidity_exit_price - entry_price) / entry_price × 100
```

主字段 `exit_date/exit_price/realized_return_pct` 保留"假设止损价可成交"口径（与止损逻辑一致），
保守估计通过 `detail.liquidity_*` 三字段存储，报告可同时展示两种口径。

**报告标注**：
- 统计"跌停锁仓"笔数及其占比
- 分别展示"假设可止损"和"跌停锁仓保守估计"两组数据
- 报告头部注明"止损收益假设可成交，未考虑跌停流动性风险"

### 4.2 交易成本

短线 1-3 日对成本极其敏感，必须预留：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| buy_slippage_pct | 0.0 | 买入滑点 % |
| sell_slippage_pct | 0.0 | 卖出滑点 % |
| commission_pct | 0.0 | 佣金 %（买卖双边） |
| tax_pct | 0.0 | 印花税 %（仅卖出） |

成本计算：

```
adjusted_entry = entry_price × (1 + buy_slippage_pct/100) × (1 + commission_pct/100)
adjusted_exit  = exit_price  × (1 - sell_slippage_pct/100) × (1 - commission_pct/100) × (1 - tax_pct/100)
net_return_pct = (adjusted_exit - adjusted_entry) / adjusted_entry × 100
```

报告同时输出"扣费前/扣费后"两组指标。成本参数存入 `detail` JSONB，便于后续不同费率场景对比。

### 4.3 停牌处理

**不跳过停牌**——跳过会低估风险，回测偏乐观。

| 情况 | 处理 |
|------|------|
| entry_date 无 K 线（买入日停牌） | 跳过该信号（确实无法买入） |
| 持有期内某交易日无 K 线（中途停牌） | **顺延到复牌后第一个可交易日**，用收盘价作为退出价 |

```
suspended_in_holding = False
planned_exit = entry_date 后第 holding_days 个交易日

# 1. 检测持有期内是否有停牌（entry 到 planned_exit 之间任一天缺 K 线）
trading_days = entry_date 到 planned_exit 的交易日列表
missing_days = [d for d in trading_days if daily_kline(ts_code, d) is None]
if missing_days:
    suspended_in_holding = True
    detail.suspended_in_holding = True
    detail.suspended_dates = [d.isoformat() for d in missing_days]

# 2. 确定 actual_exit：如果 planned_exit 当天无 K 线，顺延到复牌日
actual_exit = planned_exit
if daily_kline(ts_code, planned_exit) is None:
    for d in [planned_exit+1, planned_exit+2, ...]:
        if daily_kline(ts_code, d) exists:
            actual_exit = d
            break
    detail.actual_exit_note = f"原定 {planned_exit} 停牌，顺延至 {actual_exit}"

# 3. 用 actual_exit 的收盘价计算退出
#    max_return / max_drawdown 只基于有 K 线的交易日计算
#    raw_return_pct 和 realized_return_pct 都使用 actual_exit（含停牌顺延）
detail.actual_holding_days = 有 K 线的交易日数量（含顺延部分）
```

**报告标注**：
- 统计"含停牌"笔数及占比
- 停牌顺延后的实际持有天数可能 > holding_days，报告里标注"持有天数含停牌顺延"

### 4.4 数据覆盖范围预检

回测区间 `[start, end]` 是信号日区间，但 5d 持有需要 `end` 后至少 5 个交易日行情，
停牌顺延还可能更久。

**CLI 校验**：
- 引擎启动时检查 `end` 后 max(holding_days) 个交易日是否有行情数据
- 无数据的信号仍写入行（`exit_reason='incomplete'`，`realized_return_pct=NULL`），
  便于报告统计覆盖率
- 统计 `incomplete_exit_count`（因未来行情不足无法计算的笔数）

**报告标注**：
- 报告头部提示"末尾 N 个信号因未来行情不足，部分持有期未纳入"
- 各持有期的 `valid_trades` 会自然反映这个截断

## 5. 数据库表设计

### 5.1 short_backtest_trade（每信号 × 每持有期 = 一行）

```
一个信号（signal_date + ts_code）产生 4 行（holding_days = 1, 2, 3, 5）。
```

```sql
CREATE TABLE short_backtest_trade (
    id                      SERIAL PRIMARY KEY,
    backtest_run_id         VARCHAR(36) NOT NULL,      -- 回测批次 UUID
    signal_date             DATE NOT NULL,              -- 选股信号日 T
    entry_date              DATE,                       -- 买入日 T+1（NULL=跳过）
    ts_code                 VARCHAR(12) NOT NULL,       -- 股票代码
    holding_days            INTEGER NOT NULL,           -- 计划持有天数 1/2/3/5
    exit_date               DATE,                       -- 实际退出日（含止损/停牌顺延）
    exit_price              FLOAT,                      -- 实际退出价（realized 口径）
    rule_score              NUMERIC(5,2),               -- 信号日规则综合分
    active_dims             INTEGER DEFAULT 0,          -- 命中维度数
    dim_detail              JSONB,                      -- 各维度得分 {"limit":30, "moneyflow":18, ...}
    rank_in_day             INTEGER,                    -- 当日排名（含 cap 后）
    sector_l1               VARCHAR(50),                -- 申万一级行业（信号日快照）
    entry_price             FLOAT,                      -- 买入价（T+1 open）
    raw_return_pct          FLOAT,                      -- 自然持有收益 %（不止损，扣费前）
    realized_return_pct     FLOAT,                      -- 真实收益 %（执行止损后，扣费前）
    net_raw_return_pct      FLOAT,                      -- 自然持有收益 %（不止损，扣费后）
    net_realized_return_pct FLOAT,                      -- 真实收益 %（执行止损后，扣费后）
    max_return_pct          FLOAT,                      -- 持有期内最大收益 %（相对 entry_price）
    max_drawdown_pct        FLOAT,                      -- 持有期内最大回撤 %（相对 entry_price）
    stop_hit                BOOLEAN DEFAULT FALSE,      -- 本持有期内是否触发止损
    stop_day                INTEGER,                    -- 止损触发在第几天（1-based）
    exit_reason             VARCHAR(20),                -- time_exit / stop_loss / incomplete / skipped
    detail                  JSONB,                      -- 扩展信息（含 limit_down_locked, 成本参数, 停牌日期等）

    -- 唯一约束：同一批次、同一信号、同一持有期只写一行
    CONSTRAINT uq_short_bt_signal_hold UNIQUE (backtest_run_id, signal_date, ts_code, holding_days)
);

CREATE INDEX ix_short_bt_run ON short_backtest_trade(backtest_run_id);
CREATE INDEX ix_short_bt_signal ON short_backtest_trade(signal_date);
CREATE INDEX ix_short_bt_code ON short_backtest_trade(ts_code);
CREATE INDEX ix_short_bt_hold ON short_backtest_trade(holding_days);
CREATE INDEX ix_short_bt_exit ON short_backtest_trade(exit_date);
```

### 5.2 ORM 模型

在 `storage/models.py` 中追加 `ShortBacktestTrade` 类。

## 6. 指标计算

### 6.1 按持有期分组指标

对每个 holding_days 独立计算，**默认使用 realized_return_pct**：

| 指标 | 公式 | 说明 |
|------|------|------|
| total_trades | COUNT(*) | 总行数（= 信号数，每个信号在该持有期有一行） |
| valid_trades | COUNT(realized_return_pct NOT NULL) | 有效行数 |
| win_rate | COUNT(realized > 0) / valid × 100 | 胜率 % |
| avg_return | AVG(realized_return_pct) | 平均收益率 % |
| median_return | 中位数(realized_return_pct) | 中位数收益率 % |
| avg_win | AVG(realized) WHERE realized > 0 | 平均盈利 % |
| avg_loss | AVG(realized) WHERE realized ≤ 0 | 平均亏损 % |
| payoff_ratio | avg_win / ABS(avg_loss) | 盈亏比 |
| max_return | MAX(realized_return_pct) | 单笔最大盈利 % |
| max_loss | MIN(realized_return_pct) | 单笔最大亏损 % |
| sharpe_like | avg_return / STDDEV(realized_return_pct) | 简化夏普 |
| stop_hit_rate | COUNT(stop_hit=True) / total × 100 | 止损触发率 % |
| exit_based_cum_return | 见下方公式 | 按退出日聚合的累计收益 |

**exit_based_cum_return 计算方法**（按 exit_date 聚合）：

```
# 1. exit_date 直接从表中读取（已含止损/停牌顺延修正）

# 2. 按 exit_date 聚合：每天退出的所有 trade 的 realized_return 等权平均
daily_return = {}  # exit_date → avg(realized_return_pct)
for exit_date, group in trades.group_by(exit_date):
    daily_return[exit_date] = mean(group.realized_return_pct)

# 3. 复利累计（按 exit_date 时序）
exit_based_cum_return_pct = (Π(1 + daily_return[d] / 100) - 1) × 100   (d 按日期排序)
```

**命名说明**：使用 `exit_based_cum_return` 而非 `cum_return`，明确这**不是**严格的投资组合权益曲线。
它未处理多日持仓重叠和资金占用，仅作为"按退出日视角的累计收益"参考指标。
真正的 `equity_curve`（每日固定资金买入 TOP N，持仓到期/止损释放资金）作为后续增强。

**为什么按 exit_date 而非 signal_date**：
- 按 signal_date 聚合时，T 的 5d 持有期收益覆盖 T+1~T+5，与 T+1 的信号重叠
- 按 exit_date 聚合表示"每天实际回收资金的收益率"，更接近真实组合表现

### 6.2 分组分析维度

| 维度 | 说明 |
|------|------|
| 按月份 | 月度收益趋势 |
| 按 rule_score 分档 | 高分(≥40) / 中分(30-40) / 低分(<30) |
| 按维度命中数 | 1维 / 2维 / 3+维 |
| 按板块（快照） | 申万一级行业（使用 sector_l1 快照字段） |
| 按排名 | TOP 5 vs TOP 6-10 vs TOP 11-20 |
| 止损对比 | raw vs realized（止损是否有效） |
| 跌停锁仓 | limit_down_locked=True 的笔数及实际亏损 |
| 停牌影响 | suspended_in_holding=True 的笔数及顺延后收益 |

## 7. 配置

### 7.1 CLI 参数

```bash
mo-stock backtest --strategy short \
    --start 2026-01-01 \
    --end 2026-04-30 \
    --top-n 20 \              # 每日取 TOP N 候选
    --stop-loss-pct 5.0 \     # 固定止损百分比
    --holding-days 1,2,3,5    # 持有期列表
```

### 7.2 weights.yaml 新增配置段

```yaml
# 短线回测参数（新增）
short_backtest:
  stop_loss_pct: 5.0            # 固定止损百分比
  holding_days: [1, 2, 3, 5]    # 持有期列表
  skip_limit_up_gap: true       # 跳过涨停高开
  # 交易成本（短线敏感，默认 0；实盘校准时应设真实值）
  buy_slippage_pct: 0.0         # 买入滑点 %
  sell_slippage_pct: 0.0        # 卖出滑点 %
  commission_pct: 0.0           # 佣金 %（买卖双边）
  tax_pct: 0.0                  # 印花税 %（仅卖出）
```

## 8. 报告格式

### 8.1 终端输出

```
═══════════════════════════════════════
  短线策略回测报告（规则层 run-once --skip-ai 口径）
  run_id: abc-123-def
═══════════════════════════════════════
区间: 2026-01-02 → 2026-04-30 (78 交易日)
TOP N: 20  |  止损: 5.0%  |  买入: T+1 开盘价
成本: 滑点 0% / 佣金 0% / 印花税 0%
注意: 止损收益假设可成交，未考虑跌停流动性风险
注意: exit_based_cum_return 不是严格权益曲线（未处理资金重叠）

── 分持有期收益（realized，含止损）──
               1d      2d      3d      5d
───────────────────────────────────────────
胜率         52.3%   54.1%   55.8%   53.6%
平均收益     0.31%   0.52%   0.68%   0.85%
中位数收益   0.15%   0.28%   0.35%   0.42%
盈亏比       1.12    1.18    1.22    1.15
最大盈利     9.8%    15.2%   18.6%   22.3%
最大亏损    -3.2%   -4.1%   -5.0%   -5.0%
止损触发率    8.2%    10.5%   12.1%   14.8%
退出日累计   12.3%   18.6%   22.1%   28.5%
有效笔数     1520    1498    1476    1432

── 扣费后收益（佣金 0.025% / 印花税 0.05%）──
               1d      2d      3d      5d
───────────────────────────────────────────
平均收益     0.25%   0.45%   0.61%   0.78%
退出日累计   10.1%   16.2%   19.8%   25.4%

── 月度收益（以 3d 为基准）──
月份     笔数  平均收益  胜率     盈亏比
2026-01   80   0.45%   51.2%   1.08
2026-02   84   0.72%   58.3%   1.22
2026-03   82   0.81%   60.1%   1.28
2026-04   80   0.95%   63.5%   1.35

── 规则分分组 ──
rule_score ≥ 40: avg_3d=1.12%  win=62.5%  n=156
rule_score 30-40: avg_3d=0.65%  win=55.3%  n=312
rule_score < 30:  avg_3d=0.28%  win=50.1%  n=468

── 维度共振 ──
命中 3+ 维度: avg_3d=1.05%  win=61.8%  n=203
命中 2 维度:  avg_3d=0.58%  win=54.2%  n=420
命中 1 维度:  avg_3d=0.32%  win=49.8%  n=313

── 止损分析 ──
止损触发笔数: 182 / 1560 (11.7%)
止损日分布:  day1=58  day2=45  day3=39  day4=22  day5=18
跌停锁仓: 12 笔 (6.6% 止损中)，保守估计亏损 avg=-8.2% vs 假设止损 -5.0%
止损后 5d raw_return: avg=-2.1% → 止损是正确的（止损避免更大亏损）
raw vs realized 差异: avg_3d raw=0.72% vs realized=0.68% → 止损净贡献 -0.04%

── 停牌影响 ──
含停牌笔数: 23 / 1560 (1.5%)
停牌顺延后平均收益: 0.42% vs 无停牌 0.68%

── 排名有效性 ──
TOP 1-5:  avg_3d=1.05%  win=61.2%
TOP 6-10: avg_3d=0.72%  win=56.8%
TOP 11-20: avg_3d=0.41%  win=51.3%
═══════════════════════════════════════
```

### 8.2 Markdown 文件

每次回测生成 `reports/short-backtest-{run_id[:8]}.md`，内容与终端输出一致，额外包含：
- 每日 TOP N 清单及后续收益
- 单笔最大盈利/亏损明细
- 分板块详细统计表（使用 sector_l1 快照）
- 跌停锁仓明细
- 停牌顺延明细

## 9. 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/mo_stock/backtest/short_engine.py` | 新增 | 短线回测引擎 |
| `src/mo_stock/backtest/short_metrics.py` | 新增 | 多持有期指标计算 |
| `src/mo_stock/backtest/__init__.py` | 修改 | 导出 run_short_backtest |
| `src/mo_stock/scorer/combine.py` | 修改 | 提取板块 cap 为独立函数 |
| `src/mo_stock/storage/models.py` | 修改 | 追加 ShortBacktestTrade |
| `src/mo_stock/cli.py` | 修改 | backtest 命令支持 --strategy short |
| `config/weights.yaml` | 修改 | 追加 short_backtest 配置段 |
| `alembic/versions/xxx_short_backtest_trade.py` | 新增 | Alembic migration |
| `tests/unit/backtest/test_short_backtest.py` | 新增 | 回测引擎单元测试 |
| `tests/unit/backtest/test_short_metrics.py` | 新增 | 指标计算单元测试 |
| `tests/integration/test_combine_sector_cap.py` | 修改 | 提取板块 cap 后确保短线日报选股不变 |
| `docs/schema.md` | 修改 | 更新表结构文档 |
| `docs/short-backtest-plan-2026-05-07.md` | 本文件 | 设计文档 |

## 10. 复用与依赖

### 10.1 复用现有代码

| 模块 | 复用内容 |
|------|---------|
| `filters/short/*.py` | 5 个 Filter 的 score_all() |
| `scorer/combine.py` | _weighted_combine + _build_hard_reject_map + 排序 tie-breaker + 板块 cap（需先提取） |
| `backtest/engine.py` | _trade_dates_between, _next_trade_date, _future_trade_dates, _limit_up_gap_threshold |
| `backtest/metrics.py` | 扩展而非替换 |
| `storage/repo.py` | get_index_member_l1_map 等数据查询辅助函数 |

### 10.2 外部依赖

无新增依赖。全部基于 SQLAlchemy + 已有数据表。

## 11. 实施分 Phase

### Phase 0：基础设施

1. **从 combine.py 提取板块 cap 逻辑**为独立函数 `apply_sector_cap(session, scored, cfg)`
2. 新增 `ShortBacktestTrade` ORM 模型
3. 新增 Alembic migration
4. 新增 `short_backtest` 配置段
5. 从 `engine.py` 提取共享辅助函数到公共模块

### Phase 1：回测引擎

6. 实现 `short_engine.py` 核心逻辑（含止损双轨、交易成本、跌停标记、停牌顺延）
7. 实现 `short_metrics.py` 指标计算（含 exit_based_cum_return）
8. 单元测试

### Phase 2：CLI + 报告

9. CLI backtest 命令支持 `--strategy short`
10. 终端报告输出
11. Markdown 报告生成

### Phase 3：分析增强

12. 分组统计（月份 / rule_score / 维度数 / 板块 / 排名）
13. 止损分析（raw vs realized 对比）
14. 跌停锁仓分析
15. 停牌影响分析
16. 阈值校准建议输出

## 12. 注意事项

- **不做前瞻偏差**：只使用 T 日及之前的数据做选股，T+1 数据仅用于买入价
- **不调 Tushare API**：全部基于已回填数据，零网络开销
- **复刻 run-once --skip-ai**：不含 AI 层，含 tie-breaker 和板块 cap
- **板块 cap 需先提取**：当前内联在 combine_scores 中，Phase 0 必须先提取为独立函数
- **止损双轨**：raw_return 不止损，realized_return 执行止损；指标默认用 realized
- **跌停流动性**：止损日跌停时标记 `limit_down_locked`，不声称"以 low 成交"，报告分别统计
- **停牌顺延**：中途停牌不跳过，顺延到复牌后首日，标记 `suspended_in_holding`
- **每持有期一行**：同一信号产生 4 行，每行有独立的 stop_hit/stop_day/exit_reason/exit_date/exit_price
- **exit_date/exit_price 落库**：每行记录实际退出日和退出价，避免指标计算时反复推导
- **扣费后收益落库**：net_raw_return_pct / net_realized_return_pct 直接存在表中
- **exit_based_cum_return**：按退出日聚合，命名明确不是权益曲线；真正 equity_curve 作为后续增强
- **板块快照**：sector_l1 在写入时快照，避免 index_member 月度更新导致历史回测分组漂移
- **回测速度**：预计 78 个交易日 × TOP 20 × 4 持有期 ≈ 6240 行，分钟级完成
- **与 swing 回测隔离**：独立表、独立引擎，不互相影响
- **batch_id 管理**：每次回测生成 UUID，可并存多组回测结果对比
- **交易成本可配置**：默认 0，实盘校准时设真实费率，参数存入 detail 便于追溯
