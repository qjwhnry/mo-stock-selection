# mo-stock-selection

**A 股每日批量选股系统** —— 5 维度规则快筛 + Claude AI 深度分析。

## 核心目标

每个交易日收盘（15:30）后自动运行，从全市场综合分析 5 个维度，产出**次日短线（1–3 交易日）候选股清单** + 每只股票的裁决理由、建议入场价、止损位。

**本项目仅做选股与报告，不接券商、不自动下单。**

## 5 个分析维度

1. **龙虎榜**（Tushare `top_list` / `top_inst`）
2. **异动涨停**（Tushare `limit_list_d`）
3. **主力资金流向**（Tushare `moneyflow`）
4. **板块 / 行业**（Tushare `sw_daily` + `ths_member`）
5. **情绪**（Tushare `news` / `anns_d` + GTHT 研报）

## 架构概览

```
调度层 (APScheduler 15:30)
  └─→ ingest (Tushare + GTHT) → PostgreSQL
        └─→ filters (5 维度规则打分) → TOP 50
              └─→ ai.analyzer (Claude + prompt cache)
                    └─→ scorer (rule × 0.6 + ai × 0.4)
                          └─→ report (Markdown + JSON)
```

## 技术栈

- Python 3.10+ · PostgreSQL 15 · SQLAlchemy 2.x · Alembic
- Tushare Pro（Python SDK 直接 `import tushare as ts`）
- Anthropic Claude（`claude-sonnet-4-6` 默认）+ prompt caching
- APScheduler · pytest · mypy · ruff

## 快速开始

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 填入 TUSHARE_TOKEN、ANTHROPIC_API_KEY

# 3. 启动本地 PostgreSQL
docker compose up -d pg

# 4. 初始化数据库
alembic upgrade head

# 5. 首次回填 180 天历史数据（耗时 30–60 分钟）
mo-stock backfill --days 180

# 6. 跑一次当日选股
mo-stock run-once --date $(date +%F)

# 7. 常驻调度
mo-stock scheduler
```

## 项目结构

```
src/mo_stock/
├── data_sources/     # Tushare + GTHT 客户端封装
├── storage/          # SQLAlchemy models + repo
├── ingest/           # 数据采集 → 落 PG
├── filters/          # 5 维度规则打分
├── ai/               # Claude 分析 + prompt cache
├── scorer/           # 规则 + AI 融合
├── report/           # Markdown / JSON 输出
├── scheduler/        # APScheduler 入口
└── cli.py            # click 命令行

config/               # 权重与配置
alembic/              # 数据库迁移
tests/unit/           # 单元测试
tests/integration/    # 端到端回放测试
data/reports/         # 每日报告输出
```

## 开发命令

```bash
pytest                   # 单元 + 集成测试
ruff check src tests     # lint
mypy src                 # 类型检查
pylint src               # 质量检查
```

## 命令行手册

所有 `mo-stock` 子命令（`init-db` / `refresh-basics` / `refresh-cal` / `backfill` / `run-once` / `analyze` / `scheduler`）的完整参数、使用场景、典型工作流：**[docs/cli.md](docs/cli.md)**

## 数据库结构

14 张表的结构、字段类型、NULL 约束、索引、保留策略：**[docs/schema.md](docs/schema.md)**

## 分期路线

- **Phase 1 MVP**：PG + Tushare + 涨停/资金 2 维度 + MD 报告（本阶段）
- **Phase 2**：补齐龙虎榜 / 板块 / 情绪 3 维度 + 权重热调
- **Phase 3**：接 Claude + 4 段 prompt cache + APScheduler
- **Phase 4**：回测模块（T+1/T+3 胜率、期望收益、最大回撤）

## 数据策略

- **首次**：`mo-stock backfill --days 180`，一次性回填
- **每日**：调度器只拉增量 upsert 入 PG
- **读取**：filter / AI / report 全部只读 PG，不重复调 Tushare
- **保留**：原始 180 天滚动，结果表永久保留供回测
