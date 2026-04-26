# mo-stock-selection：A 股每日批量选股系统

> ⚠️ **本文档为项目早期（v1）规划，2026-04-26 起部分内容已被实际架构替代**
>
> 本 PLAN.md 记录的是项目从 0 到 1 的最初设计，包括 5 维度规则层、rule×0.6 + ai×0.4 融合公式、Phase 1-4 路线图。**作为历史/决策溯源资料保留**，但不再代表当前实现。
>
> **以下内容已被新版替代**：
> - 5 维度 → **6 维度**（拆出 theme 维度）
> - LhbFilter 简单加权 → **base 60 + seat 40 双层**（含席位身份分类）
> - `Lhb.seat` JSONB → **`lhb_seat_detail` 独立表**（PK 含 sha1 内容寻址）
>
> **当前架构与最新设计请看**：
> - [docs/architecture.md](docs/architecture.md) — 调用链路 + 文件索引
> - [docs/scoring.md](docs/scoring.md) — 6 维度评分公式
> - [docs/superpowers/plans/2026-04-26-theme-lhb-integration.md](docs/superpowers/plans/2026-04-26-theme-lhb-integration.md) — v2.1 集成 plan
>
> 后续大版本（如 v3）会重写本文档。

---

## Context

用户要在 `D:/QuantProjects/mo-stock-selection`（当前空目录）从零搭建一套 **A 股短线选股系统**，每日收盘后自动跑、综合 5 个维度、输出候选股清单 + 裁决理由。可参考邻目录 `D:/QuantProjects/mo-skills` 已有的 Tushare / GTHT 灵犀 / stock-trader 三套技能，避免重复造轮子。

经过多轮澄清，已锁定的核心决策：

| 维度 | 决策 |
|---|---|
| 使用形态 | **每日批量选股**，收盘后定时跑一次，产出次日候选 |
| 数据源 | **Tushare 主（Python 包 `import tushare as ts`）+ GTHT 辅（node subprocess）** |
| 数据库 | **PostgreSQL**（通过 SQLAlchemy + psycopg2） |
| AI 角色 | **分层**：规则层从全市场筛 TOP 50 → AI 对 TOP 50 做深度分析 |
| 持有周期 | **短线 1–3 交易日** |
| 项目范围 | **仅选股 + 报告**，不接券商、不自动下单 |
| 情绪来源 | **Tushare 新闻/公告 + GTHT 研报** |
| 语言偏好 | Python 主体（用户熟），中文注释，pytest + TDD |

5 个分析维度：**龙虎榜、异动涨停、主力资金流向、板块/行业、情绪（新闻+研报）**。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────┐
│  调度层 scheduler/    APScheduler · 每交易日 15:30  │
├─────────────────────────────────────────────────────┤
│  报告层 report/       Markdown + JSON 产出          │
├─────────────────────────────────────────────────────┤
│  综合打分层 scorer/   rule*0.6 + ai*0.4，风险一票否决│
├─────────────────────────────────────────────────────┤
│  AI 分析层 ai/        Claude Sonnet 4.6 + prompt cache│
├─────────────────────────────────────────────────────┤
│  规则筛选层 filters/  5 维度各 0–100 分 → TOP 50    │
├─────────────────────────────────────────────────────┤
│  持久化层 storage/    PostgreSQL + SQLAlchemy + Alembic│
├─────────────────────────────────────────────────────┤
│  数据采集层 data_sources/  TushareClient + GthtClient│
└─────────────────────────────────────────────────────┘
```

**单日流程**：`15:30 scheduler 触发` → `ingest 并发拉 Tushare/GTHT` → `PostgreSQL 落库` → `5 filters 并发打分` → `取 TOP 50` → `ai.analyzer 按股调 Claude（带 cache）` → `scorer 融合` → `report 产出 MD+JSON` → `日志/可选 webhook 推送`。

---

## 2. 目录结构

```
mo-stock-selection/
├── pyproject.toml               # PEP 621，pip install -e ".[dev]"
├── docker-compose.yml           # 本地起 PostgreSQL 15（开发用）
├── .env.example                 # TUSHARE_TOKEN / ANTHROPIC_API_KEY / MO_SKILLS_ROOT / DB_URL
├── README.md
├── alembic/                     # 数据库迁移脚本
│   ├── env.py
│   └── versions/
├── alembic.ini
├── config/
│   ├── settings.py              # pydantic-settings
│   └── weights.yaml             # 5 维度权重 + rule/ai 混合权重（热调）
├── src/mo_stock/
│   ├── data_sources/
│   │   ├── tushare_client.py    # 封装 `import tushare as ts` + tenacity 重试 + 节流
│   │   ├── gtht_client.py       # subprocess 调 node skill-entry.js
│   │   └── calendar.py          # 交易日 / 停牌 / ST / 次新 过滤
│   ├── storage/
│   │   ├── db.py, models.py, repo.py   # SQLAlchemy (psycopg2 驱动)
│   ├── ingest/
│   │   ├── ingest_daily.py      # K线/涨停/龙虎榜/资金流/daily_basic/sw_daily
│   │   ├── ingest_news.py       # news + anns
│   │   └── ingest_research.py   # GTHT 研报（仅对初筛 TOP 200 拉）
│   ├── filters/
│   │   ├── base.py              # Filter 抽象：score(ts_code, date, ctx) -> (score, detail)
│   │   ├── lhb_filter.py
│   │   ├── limit_filter.py
│   │   ├── moneyflow_filter.py
│   │   ├── sector_filter.py
│   │   └── sentiment_filter.py
│   ├── ai/
│   │   ├── client.py            # anthropic SDK + cache_control 控制
│   │   ├── prompts.py           # system / methodology / static / dynamic 四段
│   │   ├── analyzer.py
│   │   └── schemas.py           # pydantic 输出 schema（tool use 强约束）
│   ├── scorer/combine.py
│   ├── report/
│   │   ├── render_md.py
│   │   └── render_json.py
│   ├── scheduler/daily_job.py
│   └── cli.py                   # click: run-once / backfill / analyze / scheduler
├── tests/
│   ├── conftest.py              # fixtures: tmp_db, fake_tushare, fake_gtht, frozen_date
│   ├── unit/                    # 纯函数 filter 覆盖 + 客户端 mock
│   └── integration/             # freezegun 锁定日期 + 固定 fixture 回放
└── data/
    ├── cache/                   # Tushare/GTHT 原始 JSON 便于 replay
    ├── logs/
    └── reports/YYYY-MM-DD.md
```

---

## 3. 数据层（PostgreSQL 表）

- **存储引擎**：PostgreSQL 15（开发用 `docker-compose` 本地起；生产可接托管 PG）
- **驱动**：`psycopg2-binary`（同步，契合 APScheduler 批处理）
- **ORM / 迁移**：SQLAlchemy 2.x + Alembic（`alembic revision --autogenerate`）
- **JSON 字段**：一律用 `JSONB`（比 SQLite 的 TEXT JSON 更适合查询与索引）
- **时区**：`timestamp with time zone`，应用层默认 Asia/Shanghai
- **分区建议**：`daily_kline`、`moneyflow`、`news_raw` 量大时按 `trade_date` 做 `PARTITION BY RANGE`（Phase 4 再做）

| 表 | 关键字段 | 备注 |
|---|---|---|
| `stock_basic` | ts_code PK, name, industry, sw_l1, list_date, is_st | |
| `daily_kline` | (ts_code, trade_date) PK, OHLCV, pct_chg | `trade_date` 建 BRIN 索引 |
| `daily_basic` | turnover_rate, pe_ttm, pb, total_mv, circ_mv | |
| `lhb` | trade_date, ts_code, reason, buy/sell/net_amt, seat JSONB | seat 用 JSONB |
| `limit_list` | trade_date, ts_code, limit_type(U/D/Z), fd_amount, first_time, open_times, up_stat | |
| `moneyflow` | net_mf_amount, buy_lg_amount, sell_lg_amount | |
| `sw_daily` | sw_code, name, pct_change, turnover_rate | |
| `news_raw` / `anns_raw` | pub_time TIMESTAMPTZ, title, content, source | content 上 `tsvector` GIN 索引方便未来全文检索 |
| `research_report` | pub_date, org, rating, tp_low, tp_high, raw JSONB | GTHT |
| `filter_score_daily` | trade_date, ts_code, dim, score, detail JSONB | (trade_date, dim) 联合索引 |
| `ai_analysis` | ai_score, reasoning, risks JSONB, cache_hit, input/output_tokens | |
| `selection_result` | rule_score, ai_score, final_score, rank, picked | |

所有表在 `trade_date` 建索引；历史滚动保留 180 天（用 Alembic 编写定期清理 migration，或 pg_cron 定时删除）。

---

## 3.1 数据拉取策略：**首次回填 + 每日增量**，PG 是本地权威库

**核心原则**：Tushare 只在两个时点调用——① 首次部署 backfill；② 每日 15:30 增量。**所有后续读取（filter/AI/report）都直接查本地 PostgreSQL，不重复打 Tushare**。这样既省 Tushare 积分，又让系统在断网时仍能跑历史 replay。

### 首次回填（`mo-stock backfill --days 180`，一次性）

| 表 | 回填天数 | Tushare 接口 | 说明 |
|---|---|---|---|
| `stock_basic` | 全量 | `stock_basic` | 全 A 股基础信息，每周刷新一次即可 |
| `trade_cal` | 全量（至未来 1 年） | `trade_cal` | 交易日历 |
| `daily_kline` | **180 日** | `daily` 或 `pro_bar` | 支持 60 日均线/6 月波动率 |
| `daily_basic` | **180 日** | `daily_basic` | 换手/PE/PB/总市值 |
| `limit_list` | **60 日** | `limit_list_d` | 近期涨停统计（封板率、连板数） |
| `lhb` | **60 日** | `top_list` + `top_inst` | 龙虎榜 |
| `moneyflow` | **30 日** | `moneyflow` | 近期主力动向（3/5 日累计） |
| `sw_daily` | **60 日** | `sw_daily` | 申万板块日线 |
| `news_raw` | **30 日** | `major_news` | 重大新闻（news 降频） |
| `anns_raw` | **30 日** | `anns_d` | 公告 |
| `research_report` | **90 日** | GTHT `researchreport-skill` | 研报 |

回填一次大约 30–60 分钟（取决于 Tushare 积分限速），按接口分钟级分片执行，失败自动重试（tenacity）+ 断点续传（记录每接口最后成功 `trade_date`）。

### 每日增量（15:30 收盘后，scheduler 触发）

每日**只拉当日一天**的数据，用 `INSERT ... ON CONFLICT (...) DO UPDATE`（PG upsert）写入，绝不重拉历史：

- **当日必拉**：`daily_kline`、`daily_basic`、`limit_list`（当日涨停）、`lhb`（当日龙虎榜）、`moneyflow`、`sw_daily`
- **增量拉**：`news_raw` / `anns_raw`（按上次最大 `pub_time` 起拉到现在）、`research_report`（近 5 日，去重）
- **周度刷新**：`stock_basic`（每周一跑，更新 ST / 行业变动）、`trade_cal`（每月底追加）

### 数据保留策略

- **原始数据表（kline/basic/limit/lhb/moneyflow/sw/news/anns）**：滚动保留 **180 天**，超期数据由每周日的清理 job 删除（PG `DELETE` + `VACUUM`）
- **结果表（`filter_score_daily`、`ai_analysis`、`selection_result`）**：**永久保留**，作为 Phase 4 回测数据源
- **研报**：保留 180 天（覆盖率报价通常半年内有效）

### 读路径（filter/AI/report 怎么拿数据）

```
filters/*        ──→ storage/repo.py  ──→ PostgreSQL
ai/analyzer      ──→ storage/repo.py  ──→ PostgreSQL（不会再调 Tushare）
report/render_md ──→ storage/repo.py  ──→ PostgreSQL
```

`repo.py` 提供 `get_kline(ts_code, start, end)`、`get_recent_lhb(ts_code, days)`、`get_news_for_stock(ts_code, days)` 等方法。**ingest 层是唯一写入方；其他所有模块只读**（清晰的 CQRS 边界）。

### 首次部署流程

1. `docker compose up -d pg && mo-stock init-db`（或 `alembic upgrade head`）
2. `mo-stock backfill --days 180` — 耗时 30–60 分钟
3. 验证：`psql $DB_URL -c "SELECT count(*), min(trade_date), max(trade_date) FROM daily_kline"`
4. 之后每个交易日 15:30 scheduler 自动增量，**不再需要手动 backfill**

---

## 4. 规则层（5 维度打分，每维 0–100）

1. **lhb_filter**：当日/近 3 日上榜 + 净买正值 + 机构/知名游资白名单加权；连续上榜递减以避免过热。
2. **limit_filter**：首板 > 连板首日 > 断板反包；封板早 + 封单额大 + 开板少 = 高分；炸板 ≥2 次置 0；**当日涨停股硬规则：不推荐次日追高买**（只作为板块信号）。
3. **moneyflow_filter**：当日 `net_mf_amount > 0` 且近 3 日累计正；大单+超大单净流入占比；小单流入大单流出 = 负信号。净流出的股票**不落库**（避免被 0 分稀释综合分）。
4. **sector_filter**：`sw_daily` 所在一级/二级板块当日涨幅排名 TOP 5 加分；`ths_member` 热点概念命中加分；板块 3 日均涨幅加权。
5. **sentiment_filter**：近 7 日新闻/公告过 `analyze_sentiment.py` 词典打分；研报最新评级（"买入/强推"加分、"中性/减持"扣分）；**负面公告硬规则：立案调查/大股东减持/商誉暴雷 → 直接淘汰**。

综合规则分 = Σ(dim_score × weight) / Σ(active_weight)，其中 active_weight 仅累加**该股实际有得分的维度**（避免 MVP 阶段只有 2 维度时被 0 分稀释）。权重初值 `涨停 0.25 / 资金 0.25 / 龙虎 0.2 / 板块 0.15 / 情绪 0.15`（可在 `weights.yaml` 热调）。

---

## 5. AI 分析层（Claude + Prompt Caching）

- 模型：`claude-sonnet-4-6`（默认，成本敏感）；配置可切 `claude-opus-4-7[1m]` 做周末深度复盘。
- 每只 TOP 50 股票独立一次调用，输出结构化 JSON（pydantic schema + Claude tool use 强约束）：

  ```python
  class AiAnalysis(BaseModel):
      ai_score: int           # 0-100
      thesis: str             # 核心逻辑一段话
      key_catalysts: list[str]
      risks: list[str]
      suggested_entry: str    # 价格区间建议
      stop_loss: str
  ```

- **Prompt 四段分割 + 缓存断点**（`cache_control=ephemeral`）：

| 段 | 内容 | cache | 变动频率 |
|---|---|---|---|
| 1 system | 角色+输出规范+tool schema | **断点 1** | 几乎不变 |
| 2 methodology | 5 维度评分细则+短线口径+few-shot | **断点 2** | 周级（改 weights 时） |
| 3 stock_static | 基本信息+行业+60 日 K 线摘要+最近研报摘要 | **断点 3** | 按股 key，日级低频 |
| 4 stock_dynamic | 当日 5 维度规则分 detail + 当日新闻标题列表 | 不缓存 | 每日都变 |

单日对同股只调一次；TOP 50 中第 2 只开始段 1+2 命中率近 100%，预计节省 70%+ input token。`ai_analysis` 表记录 `cache_creation_input_tokens / cache_read_input_tokens` 便于成本监控。

---

## 6. 关键文件与跨语言桥

**需要新建/修改的核心文件：**

- [src/mo_stock/data_sources/tushare_client.py](src/mo_stock/data_sources/tushare_client.py) — 基于 `import tushare as ts` 包
- [src/mo_stock/data_sources/gtht_client.py](src/mo_stock/data_sources/gtht_client.py)
- [src/mo_stock/storage/models.py](src/mo_stock/storage/models.py) — SQLAlchemy 2.x ORM + JSONB
- [src/mo_stock/storage/db.py](src/mo_stock/storage/db.py) — psycopg2 engine
- [src/mo_stock/ai/prompts.py](src/mo_stock/ai/prompts.py)
- [src/mo_stock/ai/client.py](src/mo_stock/ai/client.py)
- [src/mo_stock/scheduler/daily_job.py](src/mo_stock/scheduler/daily_job.py)
- [alembic/env.py](alembic/env.py) + [alembic.ini](alembic.ini)
- [docker-compose.yml](docker-compose.yml) — PostgreSQL 15 服务
- [config/weights.yaml](config/weights.yaml)
- [pyproject.toml](pyproject.toml)

**Tushare 使用方式**（用户明确要求）：直接 `import tushare as ts`，`pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])`。不做 REST 封装，直接 Python 调包。封装层只负责 **重试 + 节流 + 日志 + 落库**，不改变接口契约。所有接口显式传 `fields=` 白名单锁定契约，避免 SDK 升级静默降级。复用 `D:/QuantProjects/mo-skills/tushare-skills/scripts/stock_data_demo.py` 的初始化写法。

**GTHT 跨语言对接方式**：`subprocess + JSON stdout`（不做常驻 HTTP 服务）。`GthtClient.call(skill, tool, args)` 内部 `node <MO_SKILLS_ROOT>/gtht-skills/<skill>/skill-entry.js mcpClient call <gateway> <tool> --args <json>`，捕获 stdout → `json.loads`。首次授权由用户手动跑 `authChecker auth --channel` 生成 `gtht-entry.json`（遵从 SKILL.md）。每日 GTHT 调用 < 100 次，subprocess 200ms 开销可接受。

---

## 7. mo-skills 复用清单

| 文件 | 用途 |
|---|---|
| `D:/QuantProjects/mo-skills/tushare-skills/SKILL.md` | Tushare 80+ 接口字段说明 |
| `D:/QuantProjects/mo-skills/tushare-skills/scripts/stock_data_demo.py` | `pro_api` 初始化模板 |
| `D:/QuantProjects/mo-skills/tushare-skills/references/数据接口.md` | 积分/频率限制 |
| `D:/QuantProjects/mo-skills/gtht-skills/lingxi-researchreport-skill/skill-entry.js` | 研报入口 |
| `D:/QuantProjects/mo-skills/gtht-skills/lingxi-ranklist-skill/skill-entry.js` | 资金净流入热榜（交叉验证） |
| `D:/QuantProjects/mo-skills/stock-trader/scripts/analyze_sentiment.py` | 情绪词典，直接 import |
| `D:/QuantProjects/mo-skills/stock-trader/scripts/analyze_fund_flow.py`、`analyze_leaders.py` | 维度打分思路参考 |
| `D:/QuantProjects/mo-skills/stock-trader/scripts/simulate_trading.py` | Phase 4 回测模块骨架 |

---

## 8. 分期实施

- **Phase 1（MVP，约 1 周）**：目录骨架 + PostgreSQL + `TushareClient` + 仅 **涨停 + 主力资金** 两维度 + Markdown 报告 + `analyze` 单股 CLI。不接 AI / GTHT。用 freezegun 锁定历史日跑通端到端。**验收：对某历史交易日跑出 TOP 10 列表且数据链路全通。**
- **Phase 2（约 1 周）**：补龙虎榜 / 板块 / 情绪 3 维度；接 GTHT 研报；引入 `weights.yaml`。
- **Phase 3（约 1 周）**：接 Claude + 四段 prompt caching + tool use schema；`scorer` 融合；APScheduler 每日 15:30 自动跑。
- **Phase 4（2 周+）**：回测模块：把 `selection_result` 与 T+1/T+3 真实收益 join，算胜率 / 期望收益 / 最大回撤；用结果反向调参 `weights.yaml`。

---

## 9. 风险与 Trade-offs

| 风险点 | 应对 |
|---|---|
| Tushare 积分：`top_list / moneyflow / news` 需较高积分 | 先按活跃股 TOP 1000 预筛再拉明细；分钟级分片节流；`news` 用 `major_news` 降频 |
| AI 成本：TOP 50 × 每日一次 | Sonnet 4.6 估算 $1–2/天（cache 命中 70%+）；Opus 4.7 估算 $5–8/天。记录 token 入表监控 |
| node/Python 跨语言 | subprocess 简单但有 ~200ms 开销；< 100 次/日可接受；密集场景再切 HTTP 常驻 |
| 交易日 / 停牌 / ST | `calendar.py` 用 `pro.trade_cal`；`stock_basic.name` 含 ST/*ST 过滤；上市 < 60 日次新过滤；`exclude_st` 与 `min_list_days` 独立开关 |
| 当日涨停次日追高 | 硬规则：涨停股不入当日 `selection_result.picked`（仅作板块信号） |
| AI 输出不稳定 | tool use 强约束 schema + 重大风险关键词（立案/停牌/退市）一票否决 |
| 重跑幂等 | `combine_scores` 用 `pg_insert.on_conflict_do_update`，同日重跑自动覆盖 |

---

## 10. Verification（完工验证）

- `docker compose up -d pg && mo-stock init-db`（或 `alembic upgrade head`）：起本地 PostgreSQL，schema 就绪
- `pytest tests/unit -q`：单元测试全绿，filter 覆盖率 > 80%
- `pytest tests/integration/test_daily_flow.py -q`：集成测试用 `pytest-postgresql` 起临时 PG（或 testcontainers），freezegun 锁定到 `tests/fixtures/2025-12-15/*.json` 回放一个历史交易日，断言 `selection_result` TOP 10 非空、含当日已知领涨股
- `mo-stock run-once --date 2025-12-15 --skip-ingest`：本地 replay 一次跑通，产出 `data/reports/2025-12-15.md`，人工肉眼 review 报告结构正确、TOP N 有 thesis + risks
- `mo-stock run-once --date <今日>`：真实跑通一次（需 TUSHARE_TOKEN 和 ANTHROPIC_API_KEY），查 PG 中 `ai_analysis.cache_read_input_tokens > 0` 确认 prompt cache 生效
- `ruff check src tests && mypy src`：静态检查无 error
- 抽查 `data/reports/YYYY-MM-DD.md` 的 TOP N：每只股票有 final_score / thesis / key_catalysts / risks / entry / stop_loss 六要素齐全
- `psql $DB_URL -c "SELECT trade_date, count(*) FROM selection_result GROUP BY 1"`：数据正确落库

---

## 11. 一键启动

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 环境变量
cp .env.example .env
# 填入：TUSHARE_TOKEN、ANTHROPIC_API_KEY、
# DB_URL=postgresql+psycopg2://mo_stock:mo_stock@localhost:5432/mo_stock
# MO_SKILLS_ROOT=D:/QuantProjects/mo-skills

# 3. 起本地 PostgreSQL（首次）
docker compose up -d pg

# 4. 初始化 schema
mo-stock init-db              # 简化路径：SQLAlchemy create_all
# 或（推荐生产）：alembic upgrade head

# 5. GTHT 首次授权（一次性，由用户手动）
cd $MO_SKILLS_ROOT/gtht-skills/lingxi-researchreport-skill
node skill-entry.js authChecker auth --channel

# 6. 首次回填历史数据
mo-stock backfill --days 180

# 7. 手动跑一次
mo-stock run-once --date 2026-04-22

# 8. 单股分析（Phase 1 已支持）
mo-stock analyze 600519.SH --date 2026-04-22

# 9. 常驻调度（每交易日 15:30）
mo-stock scheduler
```

### pyproject.toml 关键依赖

```
tushare, pandas, numpy,
sqlalchemy>=2.0, psycopg2-binary, alembic,
pydantic, pydantic-settings,
anthropic>=0.40, tenacity, apscheduler,
loguru, click, pyyaml, httpx, jinja2

# dev
pytest, pytest-cov, pytest-mock, pytest-postgresql,
mypy, pylint, ruff, freezegun
```

### .env.example

```
TUSHARE_TOKEN=xxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxx
ANTHROPIC_MODEL=claude-sonnet-4-6
DB_URL=postgresql+psycopg2://mo_stock:mo_stock@localhost:5432/mo_stock
MO_SKILLS_ROOT=D:/QuantProjects/mo-skills
GTHT_ENTRY_JSON_PATH=./gtht-skill-shared/gtht-entry.json
LOG_LEVEL=INFO
```
