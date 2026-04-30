# mo-stock-selection 架构与调用链路

> 当前版本：**v2.4**（2026-04-30 波段策略 Phase 0-2 已实现）
> 双策略：**short**（6 维短线）+ **swing**（7 维波段 + market_regime 组合层控制）
> 数据库表：**24 张**（+ swing_position；结果三表加 strategy 字段隔离）

---

## 1. 分层架构图

```
┌────────────────────────────────────────────────────────────────────┐
│                         入口层 Entry                                │
│   ┌─────────────────────────┐      ┌──────────────────────────┐   │
│   │  cli.py (click 命令)     │      │  scheduler/daily_job.py  │   │
│   │  init-db / refresh-* /   │      │  APScheduler             │   │
│   │  backfill / run-once /   │      │  cron mon-fri 15:30      │   │
│   │  analyze / scheduler     │◀────▶│  + 时点断言 / 异常捕获   │   │
│   └─────────────────────────┘      └──────────────────────────┘   │
└────────────┬───────────────────────────────────────┬───────────────┘
             │                                       │
             ▼                                       ▼
┌────────────────────────────────────────────────────────────────────┐
│                   业务编排层 (run-once 流程)                         │
│                                                                    │
│  1. ingest    → DailyIngestor.ingest_one_day(skip_enhanced=...)    │
│                  ├─ CORE 7 步 (kline/basic/limit/moneyflow/lhb/sw/│
│                  │              index_daily 沪深300+上证指数)       │
│                  └─ ENHANCED 5 步 (ths/limit_concept/cmf/inst/hm)  │
│                                                                    │
│  2. score     → 按 strategy 选择 filter 集合                      │
│                  short: Limit/Moneyflow/Lhb/Sector/Theme (5 维)    │
│                  swing: Trend/Pullback/MoneyflowSwing/SectorSwing/ │
│                        ThemeSwing/Catalyst/RiskLiquidity (7 维)    │
│                  swing 额外: MarketRegimeFilter → regime_score     │
│                                                                    │
│  3. persist   → replace_filter_scores(strategy=...)               │
│                  按 strategy 隔离，重跑不互相干扰                   │
│                                                                    │
│  4. combine   → combine_scores(strategy=..., regime_score=...)    │
│                  ├─ _weighted_combine（固定分母 1.0）              │
│                  ├─ _build_hard_reject_map（硬规则）               │
│                  ├─ swing: regime 分档 → top_n / position_scale   │
│                  ├─ 板块多样化 cap（每板块 ≤ 4 只）                │
│                  └─ DELETE+INSERT selection_result（按 strategy） │
│                                                                    │
│  5. report    → render_daily_report → MD + JSON 文件               │
└────────────────────────────────────────────────────────────────────┘
             │                                       ▲
             ▼                                       │
┌────────────────────────────────────────────────────────────────────┐
│                    数据访问层 (storage)                             │
│   ┌──────────────────┐    ┌────────────────────────────────────┐   │
│   │  models.py       │    │  repo.py                           │   │
│   │  23 张表 ORM     │◀───│  upsert_*  /  get_*  helpers       │   │
│   │  (v2.1: +6 张)   │    │  conflict_cols 主键对齐校验        │   │
│   └──────────────────┘    └────────────────────────────────────┘   │
│   ┌──────────────────────────────────────────────────────────┐    │
│   │  db.py: SQLAlchemy engine + get_session()                │    │
│   └──────────────────────────────────────────────────────────┘    │
└────────────┬───────────────────────────────────────┬───────────────┘
             │                                       │
             ▼                                       ▼
┌────────────────────────────────────────────────────────────────────┐
│                    数据源层 (data_sources)                          │
│   ┌────────────────────┐  ┌──────────────────┐  ┌────────────────┐│
│   │ TushareClient      │  │ GthtClient        │  │ calendar.py    ││
│   │ - 11 个接口方法    │  │ - skill 子进程    │  │ - 交易日校验   ││
│   │ - 速率限制 + 重试  │  │ - 3 种 stdout 解析│  │ - 14 天兜底    ││
│   │ - 单例 pro_api     │  │ - 超时可配        │  │                ││
│   └────────────────────┘  └──────────────────┘  └────────────────┘│
└────────────┬───────────────────────────────────────┬───────────────┘
             │                                       │
             ▼                                       ▼
        Tushare Pro API                        国泰海通灵犀 (node)
```

---

## 2. `run-once` 完整调用链路

```
$ mo-stock run-once --date 2026-04-26

cli.py:run_once()
├─ _parse_date / _ensure_trade_date              [P2-21 校验]
│
├─ DailyIngestor().ingest_one_day(td, skip_enhanced=False)
│  │
│  ├─ CORE 步骤 (6) ─────────────────────────────
│  │  ├─ ingest_daily_kline  ──→ TushareClient.daily()       ──→ daily_kline
│  │  ├─ ingest_daily_basic  ──→ TushareClient.daily_basic() ──→ daily_basic
│  │  ├─ ingest_limit_list   ──→ TushareClient.limit_list_d()──→ limit_list
│  │  ├─ ingest_moneyflow    ──→ TushareClient.moneyflow()   ──→ moneyflow
│  │  ├─ ingest_lhb          ──→ TushareClient.top_list()    ──→ lhb
│  │  └─ ingest_sw_daily     ──→ TushareClient.sw_daily()    ──→ sw_daily
│  │
│  └─ ENHANCED 步骤 (5, v2.1 新) ────────────────
│     ├─ ingest_ths_daily        ──→ ths_daily()        ──→ ths_daily
│     ├─ ingest_limit_concept    ──→ limit_cpt_list()   ──→ limit_concept_daily
│     ├─ ingest_concept_moneyflow──→ moneyflow_cnt_ths()──→ ths_concept_moneyflow
│     ├─ ingest_top_inst         ──→ top_inst()         ──→ lhb_seat_detail
│     │                                  + hm_list 拆 set 做席位分类
│     └─ ingest_hm_detail        ──→ hm_detail()        ──→ hot_money_detail
│
├─ load_weights_yaml("config/weights.yaml")
│
├─ 5 个 Filter 并行（逻辑独立，只读 DB）
│  ├─ LimitFilter.score_all()    ──→ ScoreResult[] (limit)
│  ├─ MoneyflowFilter.score_all()──→ ScoreResult[] (moneyflow)
│  ├─ LhbFilter.score_all()      ──→ ScoreResult[] (lhb)  ★base 60 + seat 40
│  │                                   读 lhb + lhb_seat_detail，调
│  │                                   _seat_structure_score(seats, cfg)
│  ├─ SectorFilter.score_all()   ──→ ScoreResult[] (sector)
│  └─ ThemeFilter.score_all()    ──→ ScoreResult[] (theme) ★v2.1 新维度
│                                      多概念取最高 + 渐进降级
│
├─ replace_filter_scores(td, dims=[limit/moneyflow/lhb/sector/theme], all_scores)
│  └─ DELETE WHERE trade_date AND dim IN (...) → INSERT 本轮结果
│      （v2.3：避免旧维度脏数据残留，如旧版 sector_heat_bonus）
│
├─ combine_scores(td, weights, hard_reject, top_n=20, enable_ai=True, combine_cfg=...)
│  ├─ 读 filter_score_daily 当日数据 + 重建 dim_scores_map[ts_code][dim] = ScoreResult
│  ├─ _weighted_combine (固定分母 = Σ 6 维权重 = 1.0)
│  ├─ _build_hard_reject_map (ST/次新/涨停/跌停/停牌/负面公告)
│  ├─ ⭐ v2.2 AI 阶段：取未被淘汰的 TOP ai_top_n（默认 50）
│  │   └─ analyze_stock_with_ai(session, ts_code, td, dim_scores_map[ts_code])
│  │       └─ Claude 4 段 prompt cache → schemas.StockAiAnalysis → upsert ai_analysis
│  ├─ _final_score_from(rule, ai_score) — 按 rule_weight×rule + ai_weight×ai 融合
│  ├─ ⭐ 按 final_score 重排（不只是 rule_score 排序，AI 才能真正影响 TOP N）
│  ├─ ⭐ v2.3 板块多样化 cap：申万一级 max_stocks_per_sector（默认 4），仅入选股消耗名额
│  └─ DELETE selection_result + INSERT 本轮 rows
│      （v2.3：每日一份完整快照，避免旧 picked=True 残留污染报告）
│
└─ render_daily_report(td)
   └─ 生成 data/reports/2026-04-26.{md,json}
```

### 其它命令链路

| 命令 | 入口 | 核心调用 |
|------|------|---------|
| `init-db` | cli.init_db | `Base.metadata.create_all(engine)` 一键建 23 张表 |
| `refresh-basics [--with-ths] [--with-hm-list]` | cli.refresh_basics | refresh_stock_basic / refresh_index_member / refresh_ths_concept / refresh_hot_money_list |
| `refresh-cal --start ...` | cli.refresh_cal | refresh_trade_cal（年度刷一次） |
| `backfill --days 180` | cli.backfill | DailyIngestor.backfill 按日循环跑 ingest_one_day |
| `analyze 600519.SH` | cli.analyze → analyzer.analyze_stock | 复用 5 个 Filter.score_all 单股提取 |
| `scheduler [--skip-enhanced]` | cli.scheduler → start_scheduler | APScheduler cron 每周一至五 15:30 触发 run_daily_pipeline |

---

## 3. 数据库表分类（24 张）

| 类别 | 表 | 来源 | v2.4 增量 |
|------|----|----|----------|
| **基础元数据**（周/月刷新） | `stock_basic` `trade_cal` `index_member` `ths_index` `ths_member` `hot_money_list` | refresh-basics | — |
| **日频行情**（每日） | `daily_kline` `daily_basic` `sw_daily` | run-once CORE | daily_kline 含指数日线 |
| **日频异动**（每日） | `limit_list` `lhb` `moneyflow` | run-once CORE | — |
| **日频题材** | `ths_daily` `limit_concept_daily` `ths_concept_moneyflow` | run-once ENHANCED | — |
| **日频龙虎榜席位** | `lhb_seat_detail` `hot_money_detail` | run-once ENHANCED | — |
| **日频情绪** | `news_raw` `anns_raw` `research_report` | 待接入 | — |
| **结果表**（永久保留） | `filter_score_daily` `selection_result` `ai_analysis` | scorer 输出 | **+strategy 字段** |
| **波段持仓**（v2.4 新增） | `swing_position` | backtest / 实盘 | +1（mode 区分回测/实盘） |

**v2.1 schema 关键变更**：
- `Lhb.seat` JSONB 字段 DROP（v2.1 alembic migration `20260426_theme_lhb_v21.py`）
- 席位明细搬到 `lhb_seat_detail` 独立表，PK 含 `seat_key = sha1(ts_code|exalter|side|reason)` 内容寻址
  - 避免 top_inst 重跑时返回顺序变化导致 upsert 覆盖错行

---

## 4. 权重融合（核心评分公式）

### short 策略（6 维度，`config/weights.yaml`）

```
final_score = Σ(score_i × w_i) / Σ(全部权重之和 = 1.0)
```

| 维度 | 权重 | 数据源 | 上限设计 |
|------|------|--------|---------|
| `limit` 涨停 | 0.25 | limit_list | 0-100 |
| `moneyflow` 资金流 | 0.25 | moneyflow + daily_kline.amount | 0-100 |
| `lhb` 龙虎榜 | 0.20 | lhb + lhb_seat_detail | base 60 + seat 40 |
| `sector` 申万行业 | 0.10 | sw_daily + index_member | 0-100 |
| `theme` 题材 | 0.10 | ths_daily + limit_concept + cmf | 0-100 |
| `sentiment` 情绪 | 0.10 | （未实现）| — |

### swing 策略（7 维度，`config/weights_swing.yaml`）

```
final_score = Σ(score_i × w_i) / Σ(全部权重之和 = 1.0)
```

| 维度 | 权重 | 数据源 | 说明 |
|------|------|--------|------|
| `trend` 趋势结构 | 0.27 | daily_kline | MA 多头 + 量价确认 |
| `pullback` 回踩承接 | 0.13 | daily_kline | 趋势内回撤 + 重新转强 |
| `moneyflow_swing` 波段资金 | 0.20 | moneyflow | 5/10 日资金持续性 |
| `sector_swing` 行业持续性 | 0.13 | sw_daily + index_member + moneyflow 聚合 | 行业多日强度 |
| `theme_swing` 题材持续性 | 0.09 | ths_daily + ths_concept_moneyflow | 题材多日排名 |
| `catalyst` 短线催化 | 0.08 | limit_list + lhb | 断板反包 + 龙虎榜 |
| `risk_liquidity` 风险流动性 | 0.10 | daily_kline + daily_basic | 加分制质量分 |

**market_regime 组合层控制**（不进入单股权重）：
根据沪深 300 MA 趋势 + 涨跌家数计算 regime_score（0-100），分档控制
`top_n`（3-20）、`position_scale`（0.2-1.0）、`min_final_score`（30）。

**关键原则**：缺失维度按 0 计入分子，分母固定 1.0 → 严惩单维霸榜，奖励多维共振。

### LhbFilter v2.1 双层结构

```
base = 20                        # 上榜+净买入基础分
+ net_rate_tier(0-20)            # 净买入占成交分档（2%/5%/10%）
+ purity_bonus(0-12)             # 席位主导度分档（15%/30%）
+ reason_bonus(0-8)              # 上榜原因关键词

seat = institution_buy_bonus     # 机构净买 ≥1000万 → +20
+ hot_money_buy_bonus            # 知名游资净买 ≥500万 → +12
- hot_money_sell_penalty         # 知名游资净卖 ≥1000万 → -15
+ northbound_buy_bonus           # 北向净买 ≥3000万 → +8

score = clamp(base + seat, 0, 100)
detail.lhb_formula_version = 2   # 与历史 0-100 base 量纲不横比
```

### ThemeFilter v2.1 信号合成

```python
score(stock) = max over concepts of:
    ths_rank_bonus(rank in ths_daily TOP N) +
    limit_concept_bonus(rank in limit_cpt_list) +
    moneyflow_bonus(net_amount > 0)
```

**取最高（不累加）**：避免沾边股（一只票挂在 10+ 概念）天然占优。
**渐进降级**：ths_daily 为空仍跑 limit_concept + moneyflow，三类全空才返回。

---

## 5. 架构亮点

1. **分层清晰**：data_source → ingest → storage → filter（只读） → scorer → report，**filter 不直接调外部 API**
2. **双策略隔离**：short / swing 共享数据层，评分逻辑、权重、报告、结果表通过 `strategy` 字段完全隔离
3. **固定分母综合分**：避免单维度极端分霸榜
4. **CORE/ENHANCED 分组**：题材/席位增强可一键 skip（`--skip-enhanced`），限速场景兜底
5. **席位明细 SHA1 主键**：top_inst 重跑顺序变化不会脏数据漂移
6. **配置热调**：权重 / 阈值 / regime 分档都在 yaml，不改代码即可调
7. **回测隔离**：swing_position 通过 `mode + backtest_run_id` 区分回测和实盘
8. **测试覆盖**：388 unit + integration tests，含 sqlite_session 共享 fixture

---

## 6. 关键文件索引

| 路径 | 作用 |
|------|------|
| [src/mo_stock/cli.py](../src/mo_stock/cli.py) | click CLI 入口（含 `--strategy` / `backtest` 命令） |
| [src/mo_stock/scheduler/daily_job.py](../src/mo_stock/scheduler/daily_job.py) | APScheduler + 时点断言 + strategy 透传 |
| [src/mo_stock/data_sources/tushare_client.py](../src/mo_stock/data_sources/tushare_client.py) | Tushare 接口封装（含 `index_daily`） |
| [src/mo_stock/data_sources/calendar.py](../src/mo_stock/data_sources/calendar.py) | 交易日 / 选股池工具 |
| [src/mo_stock/ingest/ingest_daily.py](../src/mo_stock/ingest/ingest_daily.py) | ingest 方法 + 清洗函数（含 `ingest_index_daily`） |
| [src/mo_stock/storage/models.py](../src/mo_stock/storage/models.py) | 24 张表 ORM（含 swing_position） |
| [src/mo_stock/storage/repo.py](../src/mo_stock/storage/repo.py) | upsert / 读 helpers（含 strategy 过滤） |
| [src/mo_stock/filters/limit_filter.py](../src/mo_stock/filters/limit_filter.py) | 短线涨停维度 |
| [src/mo_stock/filters/moneyflow_filter.py](../src/mo_stock/filters/moneyflow_filter.py) | 短线资金流维度 |
| [src/mo_stock/filters/lhb_filter.py](../src/mo_stock/filters/lhb_filter.py) | 龙虎榜维度 |
| [src/mo_stock/filters/sector_filter.py](../src/mo_stock/filters/sector_filter.py) | 申万行业维度 |
| [src/mo_stock/filters/theme_filter.py](../src/mo_stock/filters/theme_filter.py) | 题材维度 |
| [src/mo_stock/filters/trend_filter.py](../src/mo_stock/filters/trend_filter.py) | **swing** 趋势结构维度 |
| [src/mo_stock/filters/pullback_filter.py](../src/mo_stock/filters/pullback_filter.py) | **swing** 回踩承接维度 |
| [src/mo_stock/filters/moneyflow_swing_filter.py](../src/mo_stock/filters/moneyflow_swing_filter.py) | **swing** 波段资金维度 |
| [src/mo_stock/filters/sector_swing_filter.py](../src/mo_stock/filters/sector_swing_filter.py) | **swing** 行业持续性维度 |
| [src/mo_stock/filters/theme_swing_filter.py](../src/mo_stock/filters/theme_swing_filter.py) | **swing** 题材持续性维度 |
| [src/mo_stock/filters/catalyst_filter.py](../src/mo_stock/filters/catalyst_filter.py) | **swing** 短线催化维度 |
| [src/mo_stock/filters/risk_liquidity_filter.py](../src/mo_stock/filters/risk_liquidity_filter.py) | **swing** 风险流动性维度 |
| [src/mo_stock/filters/market_regime_filter.py](../src/mo_stock/filters/market_regime_filter.py) | **swing** 大盘环境（组合层） |
| [src/mo_stock/filters/swing_utils.py](../src/mo_stock/filters/swing_utils.py) | **swing** 工具函数（MA / ATR） |
| [src/mo_stock/scorer/combine.py](../src/mo_stock/scorer/combine.py) | 综合分 + 硬规则 + strategy 路由 + regime 控制 |
| [src/mo_stock/backtest/](../src/mo_stock/backtest/) | **swing** 回测引擎（engine / metrics） |
| [src/mo_stock/ai/](../src/mo_stock/ai/) | AI 分析（按 strategy 隔离） |
| [src/mo_stock/report/render_md.py](../src/mo_stock/report/render_md.py) | MD/JSON 报告渲染（双策略模板） |
| [config/weights.yaml](../config/weights.yaml) | short 策略权重 + 子参数 |
| [config/weights_swing.yaml](../config/weights_swing.yaml) | **swing** 策略权重 + regime 控制 + ATR 止损 |
| [alembic/versions/20260426_theme_lhb_v21.py](../alembic/versions/20260426_theme_lhb_v21.py) | v2.1 schema migration |
| [alembic/versions/20260430_strategy_swing_phase0.py](../alembic/versions/20260430_strategy_swing_phase0.py) | v2.4 strategy 字段 + swing_position |

详细评分公式：[docs/scoring.md](scoring.md)
CLI 完整手册：[docs/cli.md](cli.md)
表结构与字段：[docs/schema.md](schema.md)
最近一次审计：[docs/audit-2026-04-26.md](audit-2026-04-26.md)
