# mo-stock 命令行手册

> 相关文档：[打分规则详解](./scoring.md) | [数据库表结构](./schema.md)

`mo-stock-selection` 所有可执行入口统一通过 `click` 命令组暴露。

- **入口点**：`pyproject.toml` 中声明 `mo-stock = "mo_stock.cli:cli"`
- **两种调用方式**（效果等价）：
  ```bash
  mo-stock <subcommand> [options]            # pip install -e . 之后可用
  python -m mo_stock.cli <subcommand> [options]
  ```
- 所有子命令入口在 [src/mo_stock/cli.py](../src/mo_stock/cli.py)

---

## 命令总览

| 命令 | 作用 | 使用场景 | 写库？ |
|------|------|----------|--------|
| [`init-db`](#init-db) | 建表（SQLAlchemy create_all） | 首次部署 / 开发环境 | ✓ |
| [`refresh-basics`](#refresh-basics) | 刷新股票基础 + 申万行业映射 | 每周定期 / 首次 | ✓ |
| [`refresh-cal`](#refresh-cal) | 刷新交易日历 | 首次 / 每年末 | ✓ |
| [`backfill`](#backfill) | 一次性回填历史数据 | 首次部署 / 补历史 | ✓ |
| [`run-once`](#run-once) | 端到端跑一次选股 | 日常手工触发 | ✓ |
| [`run-once --strategy swing`](#swing-策略命令) | 波段选股（7 维度 + market_regime） | 波段策略日常触发 | ✓ |
| [`analyze`](#analyze) | 单股规则层分析 | 即席查询 / 调试 | ✗ |
| [`scheduler`](#scheduler) | 启动常驻定时调度 | 生产部署 | ✓ |
| [`scheduler --strategy swing`](#swing-策略命令) | 波段定时任务 | 波段策略生产部署 | ✓ |
| [`backtest --strategy swing`](#swing-策略命令) | 波段回测 | 波段策略回测验证 | ✓ |

---

## 运行频率速查

不同命令对应不同的「数据变化速率」，**不要混着用同一个 cron 节奏**。

| 命令 | 推荐频率 | 触发时机 | 写入表（量级） |
|------|---------|---------|--------------|
| `init-db` | **一次性** | 首次部署，或 schema 变更后重建 | 全表（建表 / 重建） |
| `refresh-cal` | **每年末 1 次** | 12 月末，刷新次年交易日 | `trade_cal`（~250 行/年） |
| `refresh-basics` | **每周 1 次** | 周一开盘前 | `stock_basic`（~5500 行）+ `index_member`（~5700 行） |
| `refresh-basics --with-ths` | **每月 1 次** | 月初首个交易日前 | 上述 + `ths_index`（~408 行）+ `ths_member`（~7 万行） |
| `backfill` | **一次性 / 按需** | 首次部署回填 180 天；缺数据时补区间 | 全部日频表（千万级） |
| `run-once` | **每个交易日 1 次** | 收盘后 15:30+，手工或调度触发 | 当日全部日频表 + 选股结果 |
| `scheduler` | **常驻** | 生产环境 `systemd`/`docker` 启动 | 自动调用 `run-once` |
| `analyze` | **按需** | 复盘单股、调试某次打分 | ✗ 只读 |

**为什么 `refresh-basics` 不每天跑？**

它维护的是「这只股是谁、属于哪个申万行业」这种**慢变量元数据**：
- `stock_basic`：新股 IPO ≤ 3 只/日，退市/ST 极少 → 周度足够
- `index_member`：申万行业分类**年度评审**才调整 → 月度甚至季度都行

跟「这只股今天涨了多少 / 资金流入多少」这种**日频行情**（由 `backfill` / `run-once` 维护）完全不同步，不要混。

每天跑也无害，就是浪费 ~30 秒 + 32 次 Tushare 接口配额（1 次 stock_basic + 1 次 index_classify + 31 次 index_member_all 分页）。

**典型生产节奏**（用 cron 或 scheduler）：

```
每年 12-31 23:00       refresh-cal --start <next_year>-01-01
每周一 08:00           refresh-basics
每个交易日 15:30       run-once（scheduler 自动跑，无需 cron）
```

---

## init-db

一键建表。用 SQLAlchemy 的 `Base.metadata.create_all()` 直接根据 ORM 模型建表，**适合首次部署或开发环境**。

```bash
mo-stock init-db           # CREATE 所有表（已存在的表不会重建）
mo-stock init-db --drop    # 先 DROP 再 CREATE，会有确认提示
```

**参数：**
- `--drop`：先 `DROP` 再 `CREATE`。**危险：会清空所有数据**，执行前会弹出确认。

**注意：**
- 生产环境推荐用 `alembic upgrade head` 以获得完整版本管理
- 执行完会打印 `init-db 完成：N 张表已就绪`
- **字段/表注释**已写在 `models.py` 的 `comment=` 中，`init-db` 建新表时会自动带上；对已存在的库，直接用 [docs/schema-comments.sql](schema-comments.sql) 在 psql / DataGrip 里手动跑：
  ```bash
  psql $DB_URL -f docs/schema-comments.sql
  ```

---

## refresh-basics

刷新「股票元数据」慢变量表。

```bash
mo-stock refresh-basics                            # 默认快速：约 30 秒
mo-stock refresh-basics --with-ths                 # +同花顺概念：额外 3-4 分钟
mo-stock refresh-basics --with-hm-list             # +游资名录：额外 30 秒
mo-stock refresh-basics --with-ths --with-hm-list  # 全开：约 4 分钟
```

**参数：**
- `--with-ths`（默认关闭）：同时刷新同花顺概念板块（`ths_index` + `ths_member`），耗时增加 3-4 分钟
- `--with-hm-list`（默认关闭，**v2.1 新增**）：同时刷新游资名录（`hot_money_list`），约 30 秒。LhbFilter 的席位识别依赖此表（无此表所有席位会被分类为 `other`）

**默认（约 30 秒）刷新的表：**

| 表 | 数据源 | 行数 | 内容 |
|---|---|---|---|
| `stock_basic` | Tushare `stock_basic` | ~5500 | 全 A 股基础信息（代码、名称、行业、上市日、是否 ST） |
| `index_member` | Tushare `index_classify` + `index_member_all`（按 31 个申万一级行业分页） | ~5700 | 股票 → 申万行业映射（l1/l2/l3 三级 + in_date），SectorFilter 据此把个股关联到 `sw_daily` 板块涨幅 |

**`--with-ths` 额外刷新的表：**

| 表 | 数据源 | 行数 | 内容 |
|---|---|---|---|
| `ths_index` | Tushare `ths_index`（type=N, exchange=A） | ~408 | A 股**概念板块元数据**（如「新能源车」「华为产业链」「AI 算力」） |
| `ths_member` | Tushare `ths_member`（按 408 个概念循环） | ~7 万 | 股票 → 概念**多对多**映射（一只股可属多个概念），SectorFilter 题材命中加分用 |

**建议频率：**
- 默认：**每周 1 次**（周一开盘前），或新股上市 / ST 变动 / 申万行业调整后手动触发
- `--with-ths`：**每月 1 次**（同花顺概念变化频率介于周和月之间）

**幂等性：** 所有表都是 PG `ON CONFLICT DO UPDATE` upsert，重跑安全。
- `index_member` 内部做了「跨 l1 dedupe」：少数股因 Tushare 漏标 `out_date` 出现在多个一级行业时，按 `in_date` 最新优先保留（跟到申万最近一次评审的归属）
- `ths_member` 内部做了「(ts_code, con_code) dedupe」：防御 Tushare 镜像复制 bug

**Tushare 接口与积分门槛：**
- `stock_basic`：免费
- `index_classify` / `index_member_all`：2000 积分
- `ths_index` / `ths_member`：6000 积分（且**同花顺数据有版权**，商业用途需联系同花顺授权）

---

## refresh-cal

刷新 `trade_cal` 交易日历表。

```bash
mo-stock refresh-cal --start 2024-01-01
mo-stock refresh-cal --start 2024-01-01 --end 2027-12-31
```

**参数：**
- `--start`（**必填**）：起始日期 `YYYY-MM-DD`
- `--end`（可选）：结束日期 `YYYY-MM-DD`，**默认今日 + 365 天**

**建议频率：** 每年末更新一次；调度器会用此表判断是否为交易日。

---

## backfill

一次性回填历史数据到 PostgreSQL。会按顺序执行：
1. `refresh_stock_basic`（确保股票基础表就绪）
2. `refresh_trade_cal`（确保交易日历覆盖）
3. 按日回填日频数据（K 线、涨停、资金流等）

```bash
mo-stock backfill                      # 默认回填最近 180 天
mo-stock backfill --days 365           # 回填 1 年
mo-stock backfill --days 90 --end 2026-04-22
```

**参数：**
- `--days`（默认 `180`）：回填多少天
- `--end`（可选）：回填截止日 `YYYY-MM-DD`，**默认今日**

**耗时参考：** 180 天约 30–60 分钟（受 Tushare 积分与限流影响）。

**使用场景：**
- 首次部署初始化历史数据
- 新增某段缺失区间的数据

---

## run-once

对指定交易日跑一次**端到端**选股流程：`ingest → filter → combine → report`。

```bash
mo-stock run-once                              # 跑今日
mo-stock run-once --date 2026-04-22            # 指定某个交易日
mo-stock run-once --date 2026-04-22 --skip-ingest      # 跳过采集，仅重算评分 + 报告
mo-stock run-once --date 2026-04-22 --skip-enhanced    # 只跑 7 个 CORE 步骤，跳过题材/席位增强
mo-stock run-once --date 2026-04-22 --skip-ai          # 跳过 AI 阶段，final_score 使用 rule_score
mo-stock run-once --date 2026-04-26 --force            # 允许在非交易日运行
```

**参数：**
- `--date`（可选）：选股日 `YYYY-MM-DD`，**默认今日**
- `--skip-ingest`：跳过 Tushare 数据拉取步骤。用于数据已在库、只需重算打分 / 报告的场景
- `--skip-enhanced`：只跑 7 个 CORE ingest 步骤（daily_kline / index_daily / daily_basic / limit_list / moneyflow / lhb / sw_daily），跳过 5 个 ENHANCED 步骤（ths_daily / limit_concept / concept_moneyflow / top_inst / hm_detail）。Tushare 限速时或调试用
- `--skip-ai`：跳过 combine 的 AI 分析阶段，`ai_score=None`，`final_score` 直接回退为 `rule_score`。Anthropic API 故障 / 调试 / 控制成本时使用
- `--strategy short|swing`：策略选择（默认 `short`）。`short`：5 个已实现短线维度 + sentiment 预留权重；`swing`：7 维度波段选股 + market_regime 控制
- `--force`：允许在非交易日（周末 / 节假日）运行（默认会被拒绝）

**v2.2 后 AI 成本估算**（默认开启）：
- TOP 50 候选股 × 单次调用 ~3-5k input + ~600 output tokens
- 每天 ~250k input + 30k output tokens（cache 命中后实际计费量约 1/3）
- 月成本 ≈ $5-15（按 Sonnet 4.6 当前价格基准；价格可能变动）

**流程细节：**
1. 数据拉取（`DailyIngestor.ingest_one_day`）—— CORE 7 步 + ENHANCED 5 步
2. 加载权重配置 `config/weights.yaml`
3. 规则层打分（v2.1 起 5 维度：`limit` + `moneyflow` + `lhb` + `sector` + `theme`）
4. 综合打分（短线配置含 6 个权重项，sentiment 未接入时按 0 分进入固定分母）+ 硬规则过滤 → 取 TOP N（默认 20）
5. 渲染 Markdown + JSON 报告到 `data/reports/YYYY-MM-DD.{md,json}`

**幂等性：** 同一交易日重跑会 `upsert` `selection_result`，不会报唯一键冲突。

---

### 题材增强工作流（v2.1）

首次部署或周期性同步：

```bash
mo-stock refresh-basics --with-ths --with-hm-list
```

- `--with-ths`：刷 `ths_index`（408 概念）+ `ths_member`（7 万成分）
- `--with-hm-list`：刷 `hot_money_list`（109 游资）

每日运行（自动跑全部 11 个 ingest 步骤）：

```bash
mo-stock run-once --date 2026-04-26
```

**降级模式**（Tushare 限速或调试时）：

```bash
mo-stock run-once --date 2026-04-26 --skip-enhanced
```

只填 daily_kline（含指数日线）/ daily_basic / limit_list / moneyflow / lhb / sw_daily 这些核心表。
ThemeFilter 在这种模式下会因 ths_daily / limit_concept_daily 缺数据而无打分，
但其它 4 维度仍正常工作（题材维度按 0 计入综合分）。

### AI 融合工作流（v2.2）

默认开启 AI 分析（无需任何额外参数）：

```bash
mo-stock run-once --date 2026-04-26
```

流程：规则层候选 → TOP 50 候选 → Claude AI 分析 → 有 ai_score 时按 rule × 0.6 + ai × 0.4 融合，
无 ai_score 时 final_score = rule_score → 重排 TOP 20 → 报告。

**AI 缺失的优雅降级**：单股 AI 失败 → ai_score=None → final_score 降级为 rule_score。
其它股仍能正常入选，AI 失败不阻断流程。

**整体跳过 AI**：

```bash
mo-stock run-once --date 2026-04-26 --skip-ai
```

跳过后所有候选的 `ai_score` 都为 NULL，`final_score` 直接使用 `rule_score`。
Anthropic API 故障时可以立即降级使用。

**报告"选出原因"**（自动生成，无需配置）：
- markdown：每只入选股展示 AI 论点 + 关键信号 + 选出维度证据表（按 strategy 翻译已命中维度 detail）+ 操作建议（入场/止损）+ 风险
- JSON：含结构化 `rationale` 字段供下游消费

---

## analyze

对**单只股票**在指定交易日跑一次规则层分析。**不写库**、**不做 TOP N 排序**，仅返回该股的打分细节与硬规则判定。

```bash
mo-stock analyze 600519.SH                          # 分析今日
mo-stock analyze 600519.SH --date 2026-04-22        # 指定某交易日
mo-stock analyze 600519.SH --date 2026-04-22 --json # JSON 结构化输出
```

**参数：**
- `TS_CODE`（**必填**，位置参数）：股票代码，如 `600519.SH` / `000001.SZ`
- `--date`（可选）：分析的交易日 `YYYY-MM-DD`，**默认今日**
- `--json`：以 JSON 格式输出（便于脚本解析）

**输出字段：**
- 基础信息（名称、行业、申万一级、上市日、是否 ST）
- 当日 K 线（开高低收、涨跌幅、成交量/额）
- 每个维度的得分与 `detail` 细节
- 规则综合分 `rule_score`
- 硬规则命中情况

**使用场景：**
- 选股复盘：为什么某只股票没入 TOP 20？看硬规则原因
- 单股调试：交互式检查某次打分细节
- 外部脚本：`--json` 输出方便接管道

**注意：** 该命令只读当前库中的数据，不会去拉 Tushare。数据缺失时需先跑 `backfill` 或 `run-once`。

---

## scheduler

启动常驻调度进程：**每个交易日 15:30 自动执行 run-once**（基于 APScheduler）。

```bash
mo-stock scheduler
```

**参数：** 无

**部署建议：**
- 前台跑适合本地调试
- 生产用 `systemd` / `supervisor` / Docker 常驻
- 调度器会读 `trade_cal` 判断交易日，非交易日自动跳过

**相关代码：** [src/mo_stock/scheduler/daily_job.py](../src/mo_stock/scheduler/daily_job.py)

---

## 典型工作流

### 首次部署

```bash
# 1. 依赖与环境
pip install -e ".[dev]"
cp .env.example .env          # 填 TUSHARE_TOKEN、ANTHROPIC_API_KEY、DB_URL
docker compose up -d pg       # 本地 PG（可选）

# 2. 建表
alembic upgrade head          # 或 mo-stock init-db

# 3. 回填历史
mo-stock backfill --days 180  # 30–60 分钟

# 4. 首次选股
mo-stock run-once

# 5. 常驻调度
mo-stock scheduler
```

### 日常复盘

```bash
# 看今日 TOP 20 为什么选这些
cat data/reports/$(date +%F).md

# 为什么某只股票没进？
mo-stock analyze 600519.SH --date $(date +%F)

# 改了权重配置后重新打分（不重拉数据）
mo-stock run-once --date $(date +%F) --skip-ingest
```

### 异常恢复

```bash
# 数据缺失某天 → 单日回填
mo-stock backfill --days 1 --end 2026-04-22

# 调度错过 → 手工补跑
mo-stock run-once --date 2026-04-22
```

---

## swing 策略命令

### 波段选股

    mo-stock run-once --strategy swing --date 2026-04-30

使用 `config/weights_swing.yaml` 配置，7 维度打分 + market_regime 组合层控制。

### 波段回测

    mo-stock backtest --strategy swing --start 2025-01-01 --end 2026-04-30

回测结果写入 `swing_position(mode='backtest')`，不影响实盘数据。
输出 T+5/T+10/T+20 胜率、盈亏比、最大回撤等指标。

### 波段定时任务

    mo-stock scheduler --strategy swing

每交易日 15:30 触发波段选股流程。swing AI prompt 未接入前会自动跳过 AI 阶段。
