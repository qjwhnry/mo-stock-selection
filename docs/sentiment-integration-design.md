# 情绪维度接入方案：quant-data-agent 新闻同步

> 日期：2026-04-27
> 状态：待实现

## 一、背景

mo-stock-selection 的情绪维度（sentiment，权重 0.10）自项目创建以来一直未实现。
当前 `NewsRaw` 表结构设计基于 Tushare `major_news`/`news` 接口，但这两个接口存在以下问题：

- **不返回 `ts_code`**：新闻是全市场级别，无法直接按股票查询
- **字段名不一致**：接口返回 `src`，表字段是 `source`；`news` 接口返回 `datetime` 而非 `pub_time`
- **无情绪评分**：原始新闻需要额外的情感分析步骤

quant-data-agent 项目已实现完整的新闻处理流水线（RSSHub 拉取 → AI 分析 → 情绪/股票关联），可作为高质量的情绪数据源。

## 二、方案选型

| 维度 | A 直读库 | B Webhook | **C 定时同步（选定）** |
|------|---------|-----------|----------------------|
| 实现复杂度 | 低 | 中 | 中 |
| 耦合度 | 高（表结构依赖） | 低 | 低 |
| 可回测 | 不支持 | 支持 | **支持** |
| 数据冗余 | 无 | 有 | 有 |
| 运维依赖 | 必须在线 | 不必须 | **不必须** |
| 匹配批处理模式 | 否 | 否 | **是** |

**选择方案 C 的核心理由**：

1. mo-stock-selection 是 CLI 每日批处理模式，不是常驻服务，webhook 反而不自然
2. 同步后数据在自身库中，回测框架和现有查询 `get_news_for_stock()` 无缝使用
3. 即使 quant-data-agent 暂时不可用，历史同步的数据仍可使用，其他 5 个维度不受影响

## 三、架构设计

### 3.1 整体数据流

```
quant-data-agent (生产者)                   mo-stock-selection (消费者)
┌──────────────────────┐                  ┌──────────────────────────────┐
│ RSSHub 拉取 (10s轮询)  │                  │                              │
│   ↓                   │                  │ run-once --date 2026-04-27   │
│ 预筛 + AI 分析         │                  │   ↓                          │
│   ├ sentiment         │                  │ 1. sync_news(days=7)         │
│   ├ related_stocks    │───── 同步 ──────→ │    → 读 agent 库             │
│   ├ credibility       │  (选股前执行一次)  │    → 拆行 UPSERT news_raw    │
│   └ risk_flags        │                  │   ↓                          │
│   ↓                   │                  │ 2. 6 维度规则打分              │
│ PostgreSQL 存储        │                  │    含 SentimentFilter (新)    │
│                      │                  │   ↓                          │
│ news_events           │                  │ 3. combine → AI 深度分析      │
│ analysis_results      │                  │   ↓                          │
└──────────────────────┘                  │ 4. 报告输出                    │
                                          └──────────────────────────────┘
```

### 3.2 数据同步流程

```
sync_news_from_agent(days=7)
  │
  ├─ 1. 连接 quant-data-agent PostgreSQL
  │     SELECT e.event_id, e.source, e.published_at, e.title, e.content,
  │            a.sentiment, a.sentiment_strength, a.credibility_score,
  │            a.related_stocks_json, a.confidence
  │     FROM news_events e
  │     JOIN analysis_results a ON e.event_id = a.event_id
  │     WHERE a.passed = 1
  │       AND e.published_at >= :start_time
  │
  ├─ 2. 按 related_stocks 拆行
  │     一条新闻 × N 只关联股票 = N 条 news_raw 记录
  │
  ├─ 3. 计算情绪分
  │     sentiment_score = map_sentiment(sentiment, strength, impact_direction)
  │
  └─ 4. UPSERT news_raw
        ON CONFLICT (title, pub_time) DO UPDATE
```

### 3.3 字段映射

| quant-data-agent | mo-stock-selection `news_raw` | 映射规则 |
|---|---|---|
| `analysis.related_stocks[i].code` | `ts_code` | 拆行，每行一只股票 |
| `analysis.sentiment` + `sentiment_strength` + `impact_direction` | `sentiment_score` | 见 3.4 计算公式 |
| `event.title` | `title` | 直接映射 |
| `event.content` | `content` | 直接映射 |
| `event.source` | `source` | 直接映射 |
| `event.published_at` | `pub_time` | ISO-8601 → datetime |

### 3.4 情绪分计算公式

```python
def map_sentiment_score(
    sentiment: str,          # bullish / bearish / neutral
    strength: float,         # [0.0, 1.0]
    impact_direction: str,   # positive / negative / neutral
    credibility: float,      # [0.0, 1.0]
) -> float:
    """将 AI 分析结果映射为 [-1.0, 1.0] 的情绪分。"""

    # 基础方向分
    direction_map = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    base = direction_map.get(sentiment, 0.0)

    # 个股影响方向修正
    impact_map = {"positive": 0.2, "neutral": 0.0, "negative": -0.2}
    impact_adj = impact_map.get(impact_direction, 0.0)

    # 综合分 = 方向 × 强度 + 个股修正，再乘以可信度衰减
    score = (base * strength + impact_adj) * credibility

    return max(-1.0, min(1.0, score))
```

**示例**：

| sentiment | strength | impact_direction | credibility | 结果 |
|-----------|----------|------------------|-------------|------|
| bullish | 0.8 | positive | 0.9 | +0.72 |
| bullish | 0.5 | neutral | 0.7 | +0.35 |
| bearish | 0.9 | negative | 0.85 | -0.85 |
| neutral | 0.3 | neutral | 0.6 | 0.00 |

## 四、SentimentFilter 设计

### 4.1 评分逻辑

SentimentFilter 作为第 6 个维度，继承 `FilterBase`，输出 0-100 的 `ScoreResult`。

```
输入：ts_code, trade_date
处理：
  1. 查询 news_raw 获取近 N 日新闻（复用 get_news_for_stock）
  2. 按情绪分加权计算综合情绪得分
  3. 映射到 [0, 100] 区间
输出：ScoreResult(dim="sentiment", score=65, detail={...})
```

### 4.2 评分公式

```python
def score(self, ts_code: str, trade_date: date) -> ScoreResult:
    news_list = get_news_for_stock(session, ts_code, trade_date, days=3)

    if not news_list:
        return ScoreResult(score=50, detail={"reason": "无新闻数据"})  # 中性分

    # 时间衰减：近 1 天权重 1.0，2 天 0.7，3 天 0.4
    weights = [1.0, 0.7, 0.4]

    # 加权情绪分
    weighted_sum = 0.0
    total_weight = 0.0
    for i, news in enumerate(news_list):
        w = weights[min(i, len(weights) - 1)]
        weighted_sum += news.sentiment_score * w
        total_weight += w

    avg_sentiment = weighted_sum / total_weight  # [-1.0, 1.0]

    # 映射到 [0, 100]：50 为中性
    score = 50 + avg_sentiment * 50  # 利好新闻最高 100，利空最低 0

    return ScoreResult(
        ts_code=ts_code,
        trade_date=trade_date,
        dim="sentiment",
        score=score,
        detail={"news_count": len(news_list), "avg_sentiment": avg_sentiment},
    )
```

### 4.3 硬规则

在 `combine.py` 的硬规则过滤中，新增情绪相关的硬性淘汰：

```yaml
# config/weights.yaml 新增
hard_rules:
  # ... 现有规则 ...
  negative_sentiment_threshold: -0.7  # 单条新闻情绪分低于 -0.7 时淘汰
```

## 五、配置设计

### 5.1 mo-stock-selection 新增配置项

```yaml
# config/weights.yaml
dimensions:
  sentiment:
    weight: 0.10
    sync_days: 7              # 同步最近 N 天新闻
    score_lookback_days: 3    # 打分回看 N 天
    negative_threshold: -0.7  # 硬淘汰阈值

# 新增：quant-data-agent 数据源配置
news_source:
  database_url: ""           # quant-data-agent PostgreSQL 连接串，从环境变量读取
  min_credibility: 0.5       # 最低可信度过滤
```

### 5.2 环境变量

```bash
# .env 新增
QUANT_DATA_AGENT_DB_URL=postgresql://user:pass@127.0.0.1:5432/quant_news_agent
```

## 六、涉及的代码改动

### 6.1 新增文件

| 文件 | 职责 |
|------|------|
| `src/mo_stock/ingest/news_sync.py` | 新闻同步任务（连接 agent 库 → 拆行 → UPSERT news_raw） |
| `src/mo_stock/filters/sentiment.py` | SentimentFilter 实现 |

### 6.2 修改文件

| 文件 | 改动 |
|------|------|
| `src/mo_stock/ingest/ingest_daily.py` | DailyIngestor 新增 `sync_news()` 方法 |
| `src/mo_stock/scorer/combine.py` | 注册 SentimentFilter，新增硬规则 |
| `src/mo_stock/storage/repo.py` | 新增 `upsert_news_batch()` 批量写入方法 |
| `config/weights.yaml` | 新增 sentiment 配置段和 news_source 配置 |

### 6.3 不改动

| 文件 | 原因 |
|------|------|
| `src/mo_stock/storage/models.py` | NewsRaw 模型已满足需求，不需要改 |
| `src/mo_stock/data_sources/tushare_client.py` | 保留 Tushare 接口作为备用数据源 |

## 七、同步策略

### 7.1 增量同步

每次同步拉取 `published_at >= 上次同步时间 - 1天` 的数据（留 1 天 overlap 防遗漏）。
UPSERT 保证幂等：`ON CONFLICT (title, pub_time) DO UPDATE SET sentiment_score = EXCLUDED.sentiment_score`。

### 7.2 数据过期清理

`news_raw` 设计为 180 天滚动保留（与 models.py 注释一致）。
可在同步任务中顺带清理过期数据：

```sql
DELETE FROM news_raw WHERE pub_time < CURRENT_DATE - INTERVAL '180 days'
```

### 7.3 容错

- quant-data-agent 库不可连接时：**跳过同步**，记录警告日志，使用已有历史数据
- 同步部分失败时：已写入的数据不回滚，下次同步补齐
- 不影响其他 5 个维度的正常运行

## 八、实施计划

### Phase 1：数据通道（优先）

1. 新增 `news_sync.py` — 同步任务
2. 新增 `repo.upsert_news_batch()` — 批量写入
3. 修改 `DailyIngestor` — 集成同步步骤
4. 端到端验证：`mo-stock run-once` 后检查 `news_raw` 表有数据

### Phase 2：情绪打分

5. 新增 `SentimentFilter` — 情绪维度打分
6. 修改 `combine.py` — 注册第 6 维度
7. 修改 `weights.yaml` — 配置参数

### Phase 3：验证优化

8. 回测对比：接入情绪维度前后，TOP 50 命中率变化
9. 阈值调优：`negative_threshold`、`score_lookback_days` 等参数优化
10. AI prompt 更新：在 `prompts.py` 中补充情绪维度的说明

## 九、风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| quant-data-agent 的 AI 分析质量不稳定 | 情绪分不准 | 可信度权重衰减 + 硬规则兜底 |
| related_stocks 关联错误 | 新闻关联到错误的股票 | 可信度阈值过滤 + relevance=direct 优先 |
| agent 库表结构变更 | 同步失败 | 读取层做字段兼容处理，缺失字段降级为 None |
| 新闻量太大导致同步慢 | run-once 变慢 | 只同步 passed=1 的数据，且限制 N 天窗口 |
