# mo-stock-selection — 项目约定

A 股批量选股系统：**短线（short）** 5 个已实现规则维度 + `sentiment` 预留权重，
以及 **波段（swing）** 7 维度趋势选股
+ **Codex AI 深度分析**。仅做选股与报告，**不接券商、不自动下单**。

## 双策略架构

系统支持两种独立策略，通过 `--strategy` 参数切换，共享数据层但评分逻辑、权重、报告完全独立：

| 策略 | 周期 | 维度数 | 权重文件 |
|------|------|--------|---------|
| `short`（默认） | 1-3 交易日 | 5 个已实现维度（limit / moneyflow / lhb / sector / theme）+ sentiment 预留权重 | `config/weights.yaml` |
| `swing` | 5-20 交易日 | 7 维 + market_regime 组合层控制 | `config/weights_swing.yaml` |

三表（`selection_result` / `filter_score_daily` / `ai_analysis`）通过 `strategy` 字段隔离。
波段持仓状态由 `swing_position` 表管理（区分 `backtest` / `live` 模式）。

## AI 层流程

```
规则层维度 → 综合分排序 → 取 TOP 50 → analyze_stock_with_ai (Codex SDK + 4 段 prompt cache)
  → ai_score 落库 ai_analysis 表（按 strategy 隔离）
  → final_score = rule × 0.6 + ai × 0.4，按 final_score 重排 TOP N
  → 报告 markdown 含完整选出原因（AI 论点 + 维度证据中文翻译 + 操作建议 + 风险）
```

`run-once --skip-ai` 跳过 AI 阶段。

## short 策略维度

| 维度 | 权重 | 数据源 |
|------|------|--------|
| `limit` 异动涨停 | 0.25 | `limit_list` |
| `moneyflow` 主力资金流向 | 0.25 | `moneyflow` + `daily_kline.amount` |
| `lhb` 龙虎榜（base 60 + seat 40） | 0.20 | `lhb` + `lhb_seat_detail` |
| `sector` 申万一级行业 | 0.10 | `sw_daily` + `index_member` |
| `theme` 同花顺概念 + 涨停最强 + 资金流 | 0.10 | `ths_daily` + `limit_concept_daily` + `ths_concept_moneyflow` |
| `sentiment` 新闻公告 | 0.10 | 预留权重，当前未实现 SentimentFilter，综合分里按缺失维度 0 分处理 |

注意：`config/weights.yaml` 仍保留 6 个权重项，是为了固定分母和未来接入 sentiment 时保持
权重迁移简单；当前 `run_once` 实际只运行前 5 个短线 filter。

## swing 策略 7 维度

| 维度 | 权重 | 说明 |
|------|------|------|
| `trend` 趋势结构 + 量价确认 | 0.27 | MA 多头排列 + 放量突破 + 缩量回踩 |
| `pullback` 回踩承接 | 0.13 | 趋势内健康回撤 + 重新转强 |
| `moneyflow_swing` 波段资金 | 0.20 | 5/10 日资金持续性 |
| `sector_swing` 行业持续性 | 0.13 | 行业多日强度 + 派生资金聚合 |
| `theme_swing` 题材持续性 | 0.09 | 题材多日排名 + 资金确认 |
| `catalyst` 短线催化 | 0.08 | 断板反包 + 龙虎榜机构（低权重） |
| `risk_liquidity` 风险流动性 | 0.10 | 流动性、波动率、透支度质量分 |

`market_regime`（大盘环境）不进入单股权重，作为组合层控制：根据 regime_score 分档
动态调整 `top_n`（3-20）、`position_scale`（0.2-1.0）和 `min_final_score`。
止损使用 ATR 自适应：`clamp(1.5 × ATR_pct, 4%, 10%)`。

详细设计见 [`docs/swing-strategy-plan-revised-2026-04-30.md`](docs/swing-strategy-plan-revised-2026-04-30.md)。

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
.venv/bin/python -m pytest tests/               # 单元 + 集成
.venv/bin/python -m ruff check src tests        # lint
.venv/bin/python -m mypy src                    # 类型

# CLI 入口
mo-stock init-db                            # 首次部署：建表
mo-stock refresh-basics [--with-ths]        # 周度元数据
mo-stock refresh-cal --start 2024-01-01     # 年度交易日历
mo-stock backfill --days 180                # 一次性回填（含指数日线）
mo-stock run-once --date YYYY-MM-DD [--force]  # 短线选股（默认）
mo-stock run-once --strategy swing --date YYYY-MM-DD  # 波段选股
mo-stock backtest --strategy swing --start 2025-01-01 --end 2026-04-30  # 波段回测
mo-stock analyze 600519.SH [--date ...]     # 单股调试（不写库）
mo-stock scheduler [--strategy short|swing]  # 生产常驻
```

### 关键路径

| 路径 | 作用 |
|------|------|
| `src/mo_stock/filters/` | 短线 5 维 + 波段 7 维规则打分 |
| `src/mo_stock/filters/swing_utils.py` | 波段工具函数（MA / ATR / 量比计算） |
| `src/mo_stock/scorer/combine.py` | 综合分（固定分母）+ 硬规则 + strategy 路由 + regime 控制 |
| `src/mo_stock/data_sources/tushare_client.py` | Tushare 接口封装（含 `index_daily` 指数日线） |
| `src/mo_stock/backtest/` | 波段回测引擎 + 指标计算 |
| `src/mo_stock/ai/` | AI 分析（按 strategy 隔离） |
| `config/weights.yaml` | 短线权重 + 硬规则配置 |
| `config/weights_swing.yaml` | 波段权重 + regime 控制 + ATR 止损参数 |
| `docs/scoring.md` | 综合分公式与维度细节 |
| `docs/cli.md` | CLI 子命令完整手册 |
| `docs/swing-strategy-plan-revised-2026-04-30.md` | 波段策略设计文档（v4） |

### 测试约定

- 单元测试：纯函数为主，不依赖 DB
- 集成测试：用 SQLite 内存库 + mock Tushare
- **不要** 用真实 Tushare 网络调用做 CI 测试（非确定性 + 配额浪费）

---

## 三、未尽事项 / 已知缺口

详见 [`docs/audit-2026-04-26.md`](docs/audit-2026-04-26.md)。当前最关键的几项：

1. ~~Phase 3 AI 层完整实现~~ ✅ v2.2 已完成
2. **短线阈值历史回测**（各 filter 阈值未经 IR / 胜率验证）
3. ~~ingest_one_day 中 4 个核心步骤被注释~~ ✅ v2.1 已解开
4. ~~THS 概念板块接入~~ ✅ v2.1 已接入（独立 ThemeFilter 维度）
5. ~~龙虎榜机构 / 游资分离~~ ✅ v2.1 已接入（lhb_seat_detail 表 + seat_type 分类）
6. **AI prompt 质量持续优化**（v2.2 后用真实数据观察 thesis 输出，按需迭代 prompts.py）
7. **AI 成本监控**（v2.2 后建议每周看 ai_analysis 表的 token usage 总和）
8. ~~波段策略 Phase 0-2~~ ✅ 已实现（Phase 2.5 回测校准待做）
9. **波段阈值校准**（Phase 2.5：回测结果达标后才进入 Phase 3 报告 + Phase 4 AI）
