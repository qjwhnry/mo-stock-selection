# 数据库结构说明

**对象**：PostgreSQL 15+ · SQLAlchemy 2.x ORM · ORM 定义位于 [src/mo_stock/storage/models.py](../src/mo_stock/storage/models.py)

> 所有表和字段都带中文 `COMMENT`。建完表后可在 psql 里用 `\d+ <table>` 或用 `SELECT obj_description('<table>'::regclass);` 查看。
>
> 修改模型注释后，用 `mo-stock apply-comments` 单独把 `COMMENT ON` 打到已有库（不动结构、不动数据）。

---

## 总览

按用途分 5 大类 14 张表：

| 类别 | 表 | 条数级别 | 保留策略 |
|------|----|--------|---------|
| 基础 | `stock_basic`, `trade_cal` | 千级 | 永久（周/年级刷新） |
| 行情 | `daily_kline`, `daily_basic` | 日×5k | **180 天滚动** |
| 异动 | `limit_list`, `lhb`, `moneyflow` | 日×数百 | **180 天滚动** |
| 板块 | `sw_daily` | 日×31 | **180 天滚动** |
| 情绪 | `news_raw`, `anns_raw`, `research_report` | 日×千级 | **180 天滚动** |
| 结果 | `filter_score_daily`, `ai_analysis`, `selection_result` | 日×N | **永久**（供回测） |

**命名规范**
- 表名：`snake_case`，复数去掉（`limit_list` 不是 `limit_lists`）
- 复合主键：`(ts_code, trade_date)` 是行情/异动表的标配
- 索引：`ix_<table>_<col>`；唯一约束：`uq_<table>_<key>`

---

## 1. 基础表

### `stock_basic` — A 股股票基础信息

周度刷新，约 5000+ 行。硬规则（ST 过滤、次新过滤）直接读此表。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK** | Tushare 股票代码，形如 `600519.SH` / `000001.SZ` |
| `symbol` | VARCHAR(10) | ✗ | 纯数字代码，形如 `600519` |
| `name` | VARCHAR(50) | ✗ | 股票简称 |
| `area` | VARCHAR(20) | ✓ | 所在省份 |
| `industry` | VARCHAR(50) | ✓ | Tushare 行业分类（较粗） |
| `sw_l1` | VARCHAR(50) | ✓ | 申万一级行业名称（板块维度匹配） |
| `list_date` | DATE | ✓ | 上市日期；`min_list_days` 次新过滤依赖 |
| `is_st` | BOOLEAN | ✗ | 是否 ST / *ST；`exclude_st` 硬规则依赖 |
| `updated_at` | TIMESTAMPTZ | ✗ | 本行最近刷新时间 |

**索引**：`symbol`、`industry`、`sw_l1`、`is_st`

### `trade_cal` — 交易日历

调度器判断是否交易日；硬规则回看 N 日时用此表。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `cal_date` | DATE | **PK** | 自然日日期 |
| `is_open` | BOOLEAN | ✗ | 是否交易日：True=开市 |
| `pretrade_date` | DATE | ✓ | 对应的上一个交易日 |

**索引**：`is_open`

---

## 2. 行情数据（180 天滚动）

### `daily_kline` — 日线 OHLCV

Tushare `daily` 接口，全 A 股每日一行。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK₁** | 股票代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `open` / `high` / `low` / `close` | FLOAT | ✓ | 开高低收价（元，原始价，非前复权） |
| `pre_close` | FLOAT | ✓ | 昨收盘价 |
| `pct_chg` | FLOAT | ✓ | 涨跌幅（%）= (close-pre_close)/pre_close*100 |
| `vol` | FLOAT | ✓ | 成交量（**手**，1 手=100 股） |
| `amount` | FLOAT | ✓ | 成交额（**千元**） |

**索引**：`trade_date`

### `daily_basic` — 每日基础指标

Tushare `daily_basic` 接口，换手 / PE / PB / 市值。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK₁** | 股票代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `turnover_rate` | FLOAT | ✓ | 换手率（%）= 成交量/总股本 |
| `turnover_rate_f` | FLOAT | ✓ | 自由流通换手（更能反映筹码活跃度） |
| `volume_ratio` | FLOAT | ✓ | 量比 = 当日分时均量/过去 5 日分时均量；>1 放量 |
| `pe_ttm` | FLOAT | ✓ | TTM 市盈率；亏损股为 NULL |
| `pb` | FLOAT | ✓ | 市净率 = 总市值/净资产 |
| `total_mv` / `circ_mv` | FLOAT | ✓ | 总市值 / 流通市值（**万元**） |

**索引**：`trade_date`

---

## 3. 异动数据（180 天滚动）

### `limit_list` — 涨停 / 跌停 / 炸板明细

Tushare `limit_list_d`。`LimitFilter` 打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK₁** | 股票代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `limit_type` | VARCHAR(2) | ✗ | `U`=涨停、`D`=跌停、`Z`=炸板 |
| `fd_amount` | FLOAT | ✓ | 封单金额（元）；封单越大越难打开 |
| `first_time` / `last_time` | VARCHAR(10) | ✓ | 首次/最后封板时间 `HH:MM:SS` |
| `open_times` | INT | ✓ | 打开次数；≥2 在 `LimitFilter` 一票否决 |
| `up_stat` | VARCHAR(20) | ✓ | 连板统计，如 `'2/3'`（近 2 连板、累计 3 次上榜） |
| `limit_times` | INT | ✓ | 连板数 |

**索引**：`limit_type`、`trade_date`

### `lhb` — 龙虎榜席位

Tushare `top_list` + `top_inst`。`LhbFilter` 打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `trade_date` | DATE | **PK₁** | 交易日 |
| `ts_code` | VARCHAR(12) | **PK₂** | 股票代码 |
| `name` | VARCHAR(50) | ✓ | 股票名称 |
| `close` | FLOAT | ✓ | 当日收盘价 |
| `pct_change` | FLOAT | ✓ | 当日涨跌幅（%） |
| `turnover_rate` | FLOAT | ✓ | 换手率（%） |
| `amount` | FLOAT | ✓ | 总成交额（万元） |
| `l_sell` / `l_buy` / `l_amount` | FLOAT | ✓ | 龙虎榜席位卖出/买入/总成交额（万元） |
| `net_amount` | FLOAT | ✓ | 席位净买入额（万元）= l_buy - l_sell |
| `reason` | TEXT | ✓ | 上榜原因（日涨幅偏离 7% 等） |
| `seat` | JSONB | ✓ | 席位明细 `[{name, buy, sell, net}, ...]` |

**索引**：`ts_code`

### `moneyflow` — 主力资金流向

Tushare `moneyflow`。`MoneyflowFilter` 打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK₁** | 股票代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `net_mf_amount` | FLOAT | ✓ | **主力净流入额**（万元）= 大单+超大单净流入 |
| `buy_sm_amount` / `sell_sm_amount` | FLOAT | ✓ | 小单（≤5 万元）买入/卖出 |
| `buy_md_amount` / `sell_md_amount` | FLOAT | ✓ | 中单（5-20 万元）买入/卖出 |
| `buy_lg_amount` / `sell_lg_amount` | FLOAT | ✓ | 大单（20-100 万元）买入/卖出 |
| `buy_elg_amount` / `sell_elg_amount` | FLOAT | ✓ | 超大单（>100 万元）买入/卖出 |

**索引**：`trade_date`

---

## 4. 板块数据（180 天滚动）

### `sw_daily` — 申万一级行业板块日线

Tushare `sw_daily`。`SectorFilter` 打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `sw_code` | VARCHAR(20) | **PK₁** | 申万行业代码，如 `801080.SI` |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `name` | VARCHAR(50) | ✓ | 板块名称 |
| `open` / `high` / `low` / `close` | FLOAT | ✓ | 板块开高低收点位 |
| `pct_change` | FLOAT | ✓ | 板块涨跌幅（%）；板块维度打分主要输入 |
| `vol` | FLOAT | ✓ | 板块总成交量（手） |
| `amount` | FLOAT | ✓ | 板块总成交额（万元） |
| `turnover_rate` | FLOAT | ✓ | 板块换手率（%） |

**索引**：`trade_date`

---

## 5. 情绪数据（180 天滚动）

### `news_raw` — 原始新闻

Tushare `news` / `major_news`。`SentimentFilter` 打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `ts_code` | VARCHAR(12) | ✓ | 关联股票；NULL 表示市场/政策新闻 |
| `pub_time` | TIMESTAMPTZ | ✗ | 发布时间 |
| `title` | VARCHAR(500) | ✗ | 标题 |
| `content` | TEXT | ✓ | 正文 |
| `source` | VARCHAR(100) | ✓ | 来源媒体 |
| `sentiment_score` | FLOAT | ✓ | 情绪得分（词典法，-1~1） |

**唯一约束**：`(title, pub_time)` → `uq_news_raw_title_time`

### `anns_raw` — 上市公司公告

Tushare `anns_d`。硬规则负面关键词命中源（`"立案调查"`/`"退市风险警示"` 等）。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `ann_date` | DATE | ✗ | 公告日期 |
| `title` | VARCHAR(500) | ✗ | 公告标题；负面关键词匹配此字段 |
| `url` | VARCHAR(500) | ✓ | PDF/详情页链接 |
| `ann_type` | VARCHAR(50) | ✓ | 公告类型 |

**唯一约束**：`(ts_code, ann_date, title)` → `uq_anns_key`

### `research_report` — 券商研报

国泰海通 GTHT skill 抓取。情绪维度辅助输入。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `pub_date` | DATE | ✗ | 研报发布日期 |
| `org` | VARCHAR(100) | ✓ | 研究机构名称 |
| `title` | VARCHAR(500) | ✗ | 研报标题 |
| `rating` | VARCHAR(20) | ✓ | 评级：买入/增持/中性/减持/卖出 |
| `tp_low` / `tp_high` | FLOAT | ✓ | 目标价下限/上限（元） |
| `raw` | JSONB | ✓ | GTHT 返回的完整 JSON |

**唯一约束**：`(ts_code, pub_date, org, title)` → `uq_report_key`

---

## 6. 结果表（永久保留，供回测）

### `filter_score_daily` — 规则层逐维度打分

每天每股最多 5 行（`limit`/`moneyflow`/`lhb`/`sector`/`sentiment`）。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 评分对应的交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `dim` | VARCHAR(20) | ✗ | 维度标识，5 选 1 |
| `score` | FLOAT | ✗ | 本维度得分 0-100 |
| `detail` | JSONB | ✓ | 打分细节 JSON，供报告/复盘 |

**唯一约束**：`(trade_date, ts_code, dim)` → `uq_filter_score_key`
**索引**：`(trade_date, dim)`

### `ai_analysis` — Claude AI 分析结果（Phase 3 启用）

每天每股至多 1 行。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 分析对应的交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `ai_score` | INT | ✗ | AI 给出的 0-100 综合分 |
| `thesis` | TEXT | ✗ | 投资逻辑核心陈述 |
| `key_catalysts` | JSONB | ✓ | 关键催化剂数组 |
| `risks` | JSONB | ✓ | 风险提示数组 |
| `suggested_entry` | VARCHAR(100) | ✓ | 建议入场价 / 区间 |
| `stop_loss` | VARCHAR(100) | ✓ | 止损位 |
| `model` | VARCHAR(50) | ✓ | Claude 模型 ID |
| `input_tokens` / `output_tokens` | INT | ✓ | token 用量 |
| `cache_creation_tokens` / `cache_read_tokens` | INT | ✓ | prompt cache 写/读 token |
| `created_at` | TIMESTAMPTZ | ✗ | 入库时间 |

**唯一约束**：`(trade_date, ts_code)` → `uq_ai_analysis_key`

### `selection_result` — 最终选股结果

**报告和回测的主数据源**。每日全 A 股打过分的都会落库（`picked=False` 的用于复盘），其中 `rank <= top_n` 的 `picked=True`。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 选股目标交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `rank` | INT | ✗ | TOP N 排名（1 最强）；未入选填 `0` |
| `rule_score` | NUMERIC(5,2) | ✗ | 规则层综合分 0-100 |
| `ai_score` | NUMERIC(5,2) | ✓ | AI 层综合分 0-100；Phase 3 前 NULL |
| `final_score` | NUMERIC(5,2) | ✗ | 最终分（Phase 1: = rule；Phase 3: rule*0.6+ai*0.4） |
| `picked` | BOOLEAN | ✗ | 是否入选 TOP N |
| `reject_reason` | VARCHAR(200) | ✓ | 硬规则淘汰原因；入选时 NULL |
| `created_at` | TIMESTAMPTZ | ✗ | 入库时间 |

**唯一约束**：`(trade_date, ts_code)` → `uq_selection_key`
**索引**：`(trade_date, rank)`

---

## 附录 A：如何查看某张表的 comment

```sql
-- 表注释
SELECT obj_description('daily_kline'::regclass);

-- 所有字段注释
SELECT column_name, col_description('daily_kline'::regclass, ordinal_position)
FROM information_schema.columns
WHERE table_name = 'daily_kline'
ORDER BY ordinal_position;
```

psql 内：`\d+ daily_kline`

## 附录 B：数据保留策略

- **180 天滚动**的原始数据：由调度任务（`src/mo_stock/scheduler/`）定期删除早于 `today - 180` 的行
- **永久保留**的结果表：不做清理，积累越久越利于回测；数据量估算：1 万股 × 250 交易日 × 5 年 ≈ 1250 万行，PG 单表可轻松承载

## 附录 C：相关代码

- ORM 定义：[src/mo_stock/storage/models.py](../src/mo_stock/storage/models.py)
- 读写仓储层：[src/mo_stock/storage/repo.py](../src/mo_stock/storage/repo.py)
- Engine/Session：[src/mo_stock/storage/db.py](../src/mo_stock/storage/db.py)
- 建表 CLI：`mo-stock init-db`（见 [docs/cli.md](cli.md)）
- 同步注释：`mo-stock apply-comments`
