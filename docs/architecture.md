# mo-stock-selection 架构与调用链路

> 当前版本：**v2.2**（2026-04-26 完成 AI 层接入后）
> 维度数：**6 维规则 + AI 融合**（limit / moneyflow / lhb / sector / theme / sentiment + Claude AI score）
> 数据库表：**23 张**（基础 6 + 行情 3 + 异动 3 + 题材 3 + 龙虎榜席位 2 + 情绪 3 + 结果 3，含 ai_analysis 已启用）

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
│                  ├─ CORE 6 步 (kline/basic/limit/moneyflow/lhb/sw) │
│                  └─ ENHANCED 5 步 (ths/limit_concept/cmf/inst/hm)  │
│                                                                    │
│  2. score     → 5 个 Filter.score_all() 并行打分（逻辑独立）       │
│                  LimitFilter, MoneyflowFilter, LhbFilter,          │
│                  SectorFilter, ThemeFilter (v2.1 新)               │
│                                                                    │
│  3. persist   → persist_filter_scores → filter_score_daily 表      │
│                                                                    │
│  4. combine   → combine_scores                                     │
│                  ├─ _weighted_combine（固定分母 1.0）              │
│                  ├─ _build_hard_reject_map（硬规则）               │
│                  └─ TOP N → selection_result 表                    │
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
├─ persist_filter_scores(all_scores)
│  └─ upsert filter_score_daily（dim 列含 5 类 + sentiment 缺失）
│
├─ combine_scores(td, weights, hard_reject, top_n=20, enable_ai=True, ai_top_n=None)
│  ├─ 读 filter_score_daily 当日数据 + 重建 dim_scores_map[ts_code][dim] = ScoreResult
│  ├─ _weighted_combine (固定分母 = Σ 6 维权重 = 1.0)
│  ├─ _build_hard_reject_map (ST/次新/涨停/跌停/停牌/负面公告)
│  ├─ ⭐ v2.2 AI 阶段：取未被淘汰的 TOP ai_top_n（默认 50）
│  │   └─ analyze_stock_with_ai(session, ts_code, td, dim_scores_map[ts_code])
│  │       └─ Claude 4 段 prompt cache → schemas.StockAiAnalysis → upsert ai_analysis
│  ├─ _final_score_from(rule, ai_score) — 按 rule_weight×rule + ai_weight×ai 融合
│  ├─ ⭐ 按 final_score 重排（不只是 rule_score 排序，AI 才能真正影响 TOP N）
│  └─ upsert selection_result (TOP 20 picked + 0 rank reject)
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

## 3. 数据库表分类（20 张）

| 类别 | 表 | 来源 | v2.1 增量 |
|------|----|----|----------|
| **基础元数据**（周/月刷新） | `stock_basic` `trade_cal` `index_member` `ths_index` `ths_member` `hot_money_list` | refresh-basics | +1（hot_money_list） |
| **日频行情**（每日） | `daily_kline` `daily_basic` `sw_daily` | run-once CORE | — |
| **日频异动**（每日） | `limit_list` `lhb` `moneyflow` | run-once CORE | — |
| **日频题材**（v2.1 新增） | `ths_daily` `limit_concept_daily` `ths_concept_moneyflow` | run-once ENHANCED | +3 |
| **日频龙虎榜席位**（v2.1 新增） | `lhb_seat_detail` `hot_money_detail` | run-once ENHANCED | +2 |
| **日频情绪** | `news_raw` `anns_raw` `research_report` | 待 Phase 2 接入 | — |
| **结果表**（永久保留） | `filter_score_daily` `selection_result` `ai_analysis` | scorer 输出 | — |

**v2.1 schema 关键变更**：
- `Lhb.seat` JSONB 字段 DROP（v2.1 alembic migration `20260426_theme_lhb_v21.py`）
- 席位明细搬到 `lhb_seat_detail` 独立表，PK 含 `seat_key = sha1(ts_code|exalter|side|reason)` 内容寻址
  - 避免 top_inst 重跑时返回顺序变化导致 upsert 覆盖错行

---

## 4. 6 维度权重融合（核心评分公式）

```
final_score = Σ(score_i × w_i) / Σ(全部权重之和 = 1.0)
```

| 维度 | 权重 | 数据源 | 上限设计 |
|------|------|--------|---------|
| `limit` 涨停 | 0.25 | limit_list | 0-100 |
| `moneyflow` 资金流 | 0.25 | moneyflow + daily_kline.amount | 0-100（today 50 + ratio 30 + rolling 20）|
| `lhb` 龙虎榜 | 0.20 | lhb + **lhb_seat_detail** | **base 60 + seat 40** ⭐v2.1 重排 |
| `sector` 申万行业 | 0.10 | sw_daily + index_member | 0-100 |
| `theme` 题材 ⭐v2.1 新 | 0.10 | ths_daily + limit_concept + cmf | 0-100（取最高概念加分）|
| `sentiment` 情绪 | 0.10 | （未实现）| — |

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
2. **5 个 filter 完全独立**：score_all 间无依赖，可水平并行
3. **固定分母综合分**：避免单维度极端分霸榜
4. **CORE/ENHANCED 分组**：题材/席位增强可一键 skip（`--skip-enhanced`），限速场景兜底
5. **席位明细 SHA1 主键**：top_inst 重跑顺序变化不会脏数据漂移
6. **配置热调**：6 维权重 / 各 filter 阈值都在 [config/weights.yaml](../config/weights.yaml)，不改代码即可调
7. **AI 层留位**：`_final_score_from(rule, ai)` 已有显式分支，Phase 3 接入只需补 `ai/` 目录
8. **测试覆盖**：317 unit + integration tests（v2.1 后），含 sqlite_session 共享 fixture（自动 patch JSONB→JSON）

---

## 6. 关键文件索引

| 路径 | 作用 |
|------|------|
| [src/mo_stock/cli.py](../src/mo_stock/cli.py) | click CLI 入口（7 个子命令） |
| [src/mo_stock/scheduler/daily_job.py](../src/mo_stock/scheduler/daily_job.py) | APScheduler + 时点断言 |
| [src/mo_stock/data_sources/tushare_client.py](../src/mo_stock/data_sources/tushare_client.py) | Tushare 11 个接口封装 |
| [src/mo_stock/data_sources/gtht_client.py](../src/mo_stock/data_sources/gtht_client.py) | 国泰海通 skill 子进程 |
| [src/mo_stock/data_sources/calendar.py](../src/mo_stock/data_sources/calendar.py) | 交易日 / 选股池工具 |
| [src/mo_stock/ingest/ingest_daily.py](../src/mo_stock/ingest/ingest_daily.py) | 11 个 ingest 方法 + 清洗函数 |
| [src/mo_stock/storage/models.py](../src/mo_stock/storage/models.py) | 23 张表 ORM 定义 |
| [src/mo_stock/storage/repo.py](../src/mo_stock/storage/repo.py) | upsert / 读 helpers |
| [src/mo_stock/filters/limit_filter.py](../src/mo_stock/filters/limit_filter.py) | 涨停维度 |
| [src/mo_stock/filters/moneyflow_filter.py](../src/mo_stock/filters/moneyflow_filter.py) | 资金流维度 |
| [src/mo_stock/filters/lhb_filter.py](../src/mo_stock/filters/lhb_filter.py) | 龙虎榜（v2.1 base+seat） |
| [src/mo_stock/filters/sector_filter.py](../src/mo_stock/filters/sector_filter.py) | 申万行业维度 |
| [src/mo_stock/filters/theme_filter.py](../src/mo_stock/filters/theme_filter.py) | **v2.1 新** 题材维度 |
| [src/mo_stock/scorer/combine.py](../src/mo_stock/scorer/combine.py) | 综合分 + 硬规则 |
| [src/mo_stock/report/render_md.py](../src/mo_stock/report/render_md.py) | MD/JSON 报告渲染 |
| [src/mo_stock/analyzer.py](../src/mo_stock/analyzer.py) | 单股分析（不写库） |
| [src/mo_stock/ai/](../src/mo_stock/ai/) | **v2.2 已实现**：client / schemas / prompts / analyzer 4 文件 |
| [config/weights.yaml](../config/weights.yaml) | 6 维度权重 + 子参数 |
| [config/settings.py](../config/settings.py) | pydantic-settings（.env 加载） |
| [alembic/versions/20260426_theme_lhb_v21.py](../alembic/versions/20260426_theme_lhb_v21.py) | v2.1 schema migration |

详细评分公式：[docs/scoring.md](scoring.md)
CLI 完整手册：[docs/cli.md](cli.md)
表结构与字段：[docs/schema.md](schema.md)
最近一次审计：[docs/audit-2026-04-26.md](audit-2026-04-26.md)
