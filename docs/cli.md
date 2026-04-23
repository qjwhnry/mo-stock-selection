# mo-stock 命令行手册

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
| [`refresh-basics`](#refresh-basics) | 刷新股票基础信息 | 每周定期 / 首次 | ✓ |
| [`refresh-cal`](#refresh-cal) | 刷新交易日历 | 首次 / 每年末 | ✓ |
| [`backfill`](#backfill) | 一次性回填历史数据 | 首次部署 / 补历史 | ✓ |
| [`run-once`](#run-once) | 端到端跑一次选股 | 日常手工触发 | ✓ |
| [`analyze`](#analyze) | 单股规则层分析 | 即席查询 / 调试 | ✗ |
| [`scheduler`](#scheduler) | 启动常驻定时调度 | 生产部署 | ✓ |

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

刷新 `stock_basic` 表——全 A 股基础信息（代码、名称、行业、上市日、是否 ST 等）。

```bash
mo-stock refresh-basics
```

**参数：** 无

**建议频率：** 每周 1 次，或新股上市 / ST 变动时手动触发。

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
mo-stock run-once --date 2026-04-22 --skip-ingest  # 跳过采集，仅重算评分 + 报告
```

**参数：**
- `--date`（可选）：选股日 `YYYY-MM-DD`，**默认今日**
- `--skip-ingest`：跳过 Tushare 数据拉取步骤。用于数据已在库、只需重算打分 / 报告的场景

**流程细节：**
1. 数据拉取（`DailyIngestor.ingest_one_day`）
2. 加载权重配置 `config/weights.yaml`
3. 规则层打分（MVP 仅 `limit` + `moneyflow` 两个维度）
4. 综合打分 + 硬规则过滤 → 取 TOP N（默认 20）
5. 渲染 Markdown + JSON 报告到 `data/reports/YYYY-MM-DD.{md,json}`

**幂等性：** 同一交易日重跑会 `upsert` `selection_result`，不会报唯一键冲突。

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
