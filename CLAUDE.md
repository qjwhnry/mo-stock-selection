# mo-stock-selection — 项目约定

A 股每日批量选股系统：6 维度规则快筛（v2.1 后）+ **Claude AI 深度分析（v2.2 已接入）**。
仅做选股与报告，**不接券商、不自动下单**。

## v2.2 起 AI 层流程

```
规则层 5 维 → 综合分排序 → 取 TOP 50 → analyze_stock_with_ai (Claude SDK + 4 段 prompt cache)
  → ai_score 落库 ai_analysis 表
  → final_score = rule × 0.6 + ai × 0.4，按 final_score 重排 TOP N
  → 报告 markdown 含完整选出原因（AI 论点 + 维度证据中文翻译 + 操作建议 + 风险）
```

`run-once --skip-ai` 跳过 AI 阶段，行为等同 v2.1 纯规则模式。

## 6 维度（v2.1）

| 维度 | 权重 | 数据源 |
|------|------|--------|
| `limit` 异动涨停 | 0.25 | `limit_list` |
| `moneyflow` 主力资金流向 | 0.25 | `moneyflow` + `daily_kline.amount` |
| `lhb` 龙虎榜（base 60 + seat 40） | 0.20 | `lhb` + `lhb_seat_detail` |
| `sector` 申万一级行业 | 0.10 | `sw_daily` + `index_member` |
| `theme` 同花顺概念 + 涨停最强 + 资金流 | 0.10 | `ths_daily` + `limit_concept_daily` + `ths_concept_moneyflow` |
| `sentiment` 新闻公告 | 0.10 | （未实现） |

---

## 一、关键事实（必读）

### Tushare 账号积分 = 10000

- 6000 / 5000 / 2000 门槛接口**全部可调**，积分不是约束
- 决策接入新接口时，重点判断**数据质量 / 频次上限 / 字段稳定性**，不必担心配额
- 常见接口积分参考（已知）：

| 类别 | 接口 | 积分 | 频次上限 |
|------|------|------|---------|
| 基础 | stock_basic / trade_cal / daily / daily_basic | 免费 | 120/min |
| 异动 | limit_list_d / top_list / top_inst / moneyflow | 2000 | 60/min |
| 板块 | sw_daily / index_classify / index_member_all | 2000 | — |
| 概念 | ths_index / ths_member | 6000 | 200/min |
| 情绪 | anns_d / news / major_news | 2000 | 30/min |

### 项目内置 tushare-skills（涉及接口先查这里）

`vendor/mo-skills/tushare-skills/` 下有完整 skill 文档，**任何 Tushare 接口工作都先查这里**，避免重复查官网或猜字段：

| 文件 | 作用 |
|------|------|
| [`SKILL.md`](vendor/mo-skills/tushare-skills/SKILL.md) | skill 入口，覆盖典型场景与决策路径 |
| [`references/数据接口.md`](vendor/mo-skills/tushare-skills/references/数据接口.md) | **接口手册（237 行）**——出入参、字段口径、单位、积分 |
| [`scripts/stock_data_demo.py`](vendor/mo-skills/tushare-skills/scripts/stock_data_demo.py) | 个股数据完整调用示例 |
| [`scripts/fund_data_demo.py`](vendor/mo-skills/tushare-skills/scripts/fund_data_demo.py) | 基金 / 指数调用示例 |

**应用规则**：
- 接入新 Tushare 接口前 → 先看 `references/数据接口.md` 查参数和单位
- 字段口径疑问（如 `net_mf_amount` 是万元还是元）→ grep skill 文档，**不靠记忆**
- 派发 spawn_task 涉及 Tushare 时 → 提示参考 vendor/ 下文档

---

## 二、开发约定

### 不使用 git worktree

本项目改动**直接在主仓库 main 分支**上做，方便用户本地查看 diff。
不要调用 `superpowers:using-git-worktrees` 或 `EnterWorktree` 工具。

### 命令行 / 工作流

```bash
# 测试 / 静态检查
.venv/Scripts/python.exe -m pytest tests/                  # 单元 + 集成
.venv/Scripts/python.exe -m ruff check src tests           # lint
.venv/Scripts/python.exe -m mypy src                       # 类型

# CLI 入口
mo-stock init-db                            # 首次部署：建表
mo-stock refresh-basics [--with-ths]        # 周度元数据
mo-stock refresh-cal --start 2024-01-01     # 年度交易日历
mo-stock backfill --days 180                # 一次性回填
mo-stock run-once --date YYYY-MM-DD [--force]  # 每日选股端到端
mo-stock analyze 600519.SH [--date ...]     # 单股调试（不写库）
mo-stock scheduler                          # 生产常驻
```

### 关键路径

| 路径 | 作用 |
|------|------|
| `src/mo_stock/filters/` | 5 维度规则打分（limit / moneyflow / lhb / sector + sentiment 待接入） |
| `src/mo_stock/scorer/combine.py` | 综合分（固定分母）+ 硬规则过滤 |
| `src/mo_stock/data_sources/tushare_client.py` | Tushare 接口封装（重试 + 节流 + 日志） |
| `src/mo_stock/ai/` | **v2.2 已实现**：client / schemas / prompts / analyzer |
| `config/weights.yaml` | 维度权重 + 硬规则配置（热调，无需改代码） |
| `docs/scoring.md` | 综合分公式与维度细节 |
| `docs/cli.md` | CLI 子命令完整手册 |
| `docs/audit-2026-04-26.md` | 最近一次代码 / 策略审计报告 |

### 测试约定

- 单元测试：纯函数为主，不依赖 DB
- 集成测试：用 SQLite 内存库 + mock Tushare
- **不要** 用真实 Tushare 网络调用做 CI 测试（非确定性 + 配额浪费）

---

## 三、未尽事项 / 已知缺口

详见 [`docs/audit-2026-04-26.md`](docs/audit-2026-04-26.md)。当前最关键的几项：

1. ~~Phase 3 AI 层完整实现~~ ✅ v2.2 已完成
2. **阈值历史回测**（各 filter 阈值未经 IR / 胜率验证）
3. ~~ingest_one_day 中 4 个核心步骤被注释~~ ✅ v2.1 已解开
4. ~~THS 概念板块接入~~ ✅ v2.1 已接入（独立 ThemeFilter 维度）
5. ~~龙虎榜机构 / 游资分离~~ ✅ v2.1 已接入（lhb_seat_detail 表 + seat_type 分类）
6. **AI prompt 质量持续优化**（v2.2 后用真实数据观察 thesis 输出，按需迭代 prompts.py）
7. **AI 成本监控**（v2.2 后建议每周看 ai_analysis 表的 token usage 总和）
