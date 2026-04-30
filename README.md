# mo-stock-selection

**A 股每日批量选股系统** —— 支持 `short` 短线选股与 `swing` 波段选股，共享数据层，
通过 `strategy` 字段隔离评分、AI 分析和报告结果。

当前架构与调用链路请看 [docs/architecture.md](docs/architecture.md)，评分公式与维度细则请看
[docs/scoring.md](docs/scoring.md)，CLI 完整手册请看 [docs/cli.md](docs/cli.md)。

## 核心目标

每个交易日收盘（15:30）后自动运行，从全市场综合分析候选股，产出**短线（1-3 交易日）**
或**波段（5-20 交易日）**候选清单，并为每只入选股票生成规则证据、AI 论点（如启用）、
建议入场价、止损位和风险提示。

**本项目仅做选股与报告，不接券商、不自动下单。**

## 策略与维度

| 策略 | 周期 | 当前执行维度 | 说明 |
|------|------|--------------|------|
| `short` | 1-3 交易日 | 5 个已实现维度：`limit` / `moneyflow` / `lhb` / `sector` / `theme` | `sentiment` 权重仍保留在配置中，但 SentimentFilter 尚未接入，因此当前按 0 分处理 |
| `swing` | 5-20 交易日 | 7 个维度：`trend` / `pullback` / `moneyflow_swing` / `sector_swing` / `theme_swing` / `catalyst` / `risk_liquidity` | 额外使用 `market_regime` 做组合层仓位与入选数量控制 |

## 架构概览

```
调度层 (APScheduler 15:30)
  └─→ ingest (Tushare + GTHT) → PostgreSQL
        └─→ filters (按 strategy 选择短线或波段规则层) → TOP 候选
              └─→ ai.analyzer (short 可选 Claude + prompt cache；swing 目前自动跳过 AI)
                    └─→ scorer (AI 缺失时 final_score = rule_score；AI 存在时按权重融合)
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
├── filters/          # short 5 个已实现维度 + swing 7 维规则打分
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

24 张表的结构、字段类型、NULL 约束、索引、保留策略：**[docs/schema.md](docs/schema.md)**

## 当前缺口

- `short` 的 `sentiment` 维度仍是预留权重，尚未实现独立 SentimentFilter。
- `swing` 规则层和回测已接入，AI prompt 仍未接入；CLI / scheduler 会自动跳过 swing AI。
- 波段阈值仍需用历史回测继续校准，再决定是否进入实盘提示和 AI 增强阶段。

## 数据策略

- **首次**：`mo-stock backfill --days 180`，一次性回填
- **每日**：调度器只拉增量 upsert 入 PG
- **读取**：filter / AI / report 全部只读 PG，不重复调 Tushare
- **保留**：原始 180 天滚动，结果表永久保留供回测
