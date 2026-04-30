# 数据库结构说明

**对象**：PostgreSQL 15+ · SQLAlchemy 2.x ORM · ORM 定义位于 [src/mo_stock/storage/models.py](../src/mo_stock/storage/models.py)

> 所有表和字段都带中文 `COMMENT`。建完表后可在 psql 里用 `\d+ <table>` 或用 `SELECT obj_description('<table>'::regclass);` 查看。
>
> 修改模型注释后，用 `mo-stock apply-comments` 单独把 `COMMENT ON` 打到已有库（不动结构、不动数据）。

---

## 总览

按用途分 7 大类 **24 张表**（v2.4 后）：

| 类别 | 表 | 条数级别 | 保留策略 |
|------|----|--------|---------|
| 基础元数据 | `stock_basic`, `trade_cal`, `index_member`, `ths_index`, `ths_member`, `hot_money_list` | 千~万级 | 永久（周/月级刷新） |
| 行情 | `daily_kline`, `daily_basic` | 日×5k | **180 天滚动** |
| 异动 | `limit_list`, `lhb`, `moneyflow` | 日×数百 | **180 天滚动** |
| 板块 | `sw_daily` | 日×31 | **180 天滚动** |
| 题材增强（v2.1 新） | `ths_daily`, `limit_concept_daily`, `ths_concept_moneyflow` | 日×千级 | **180 天滚动** |
| 龙虎榜席位（v2.1 新） | `lhb_seat_detail`, `hot_money_detail` | 日×百级 | **180 天滚动** |
| 情绪 | `news_raw`, `anns_raw`, `research_report` | 日×千级 | **180 天滚动** |
| 结果 | `filter_score_daily`, `ai_analysis`, `selection_result` | 日×N | **永久**（供回测） |
| 波段策略（v2.4 新） | `swing_position` | 日×百级 | **按需清理**（回测按 run_id 删除） |

**v2.1 schema 关键变更**：
- DROP `lhb.seat` JSONB 字段（席位明细搬到独立表 `lhb_seat_detail`，PK 含 `seat_key=sha1(...)` 内容寻址，避免 top_inst 重跑顺序变化导致脏数据漂移）
- 新增 6 张表（hot_money_list + 3 题材 + 2 席位明细）
- 详见 alembic migration [`20260426_theme_lhb_v21.py`](../alembic/versions/20260426_theme_lhb_v21.py)

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

### `index_member` — 股票 → 申万一/二/三级行业映射

Tushare `index_member_all`（按 31 个一级行业分页）。`SectorFilter` 关联 `sw_daily` 板块涨幅用。
约 5700 行，月度刷新或申万年度评审后手动刷。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(12) | **PK** | 股票代码 |
| `l1_code` / `l1_name` | VARCHAR | ✓ | 申万一级（如 `801080.SI` / 「电子」） |
| `l2_code` / `l2_name` | VARCHAR | ✓ | 申万二级 |
| `l3_code` / `l3_name` | VARCHAR | ✓ | 申万三级 |
| `in_date` | DATE | ✓ | 加入板块日期（用于跨 l1 dedupe，保留最新归属） |

**索引**：`l1_code`

### `ths_index` — 同花顺概念/行业板块元数据

Tushare `ths_index`（type=N, exchange=A）。约 408 个 A 股概念板块（新能源车/AI/华为产业链等）。
`ThemeFilter` 关联 `ths_daily` 用于题材热度排名。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(20) | **PK** | 同花顺板块代码，如 `885806.TI` |
| `name` | VARCHAR(50) | ✗ | 板块名称 |
| `count` | INT | ✓ | 成分股数量 |
| `exchange` | VARCHAR(10) | ✓ | 交易所（A=A 股） |
| `list_date` | DATE | ✓ | 板块创建日期 |
| `type` | VARCHAR(10) | ✓ | 类型（N=概念 / I=行业等） |

### `ths_member` — 股票 → 同花顺概念多对多映射

Tushare `ths_member`。一只股可属多个概念。约 7 万行，月度刷新。
`ThemeFilter` 通过此表把概念热度反查到个股。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(20) | **PK₁** | 概念板块代码 |
| `con_code` | VARCHAR(12) | **PK₂** | 成分股代码 |
| `con_name` | VARCHAR(50) | ✓ | 成分股名称 |
| `weight` | FLOAT | ✓ | 权重（接口当前不返） |
| `in_date` / `out_date` | DATE | ✓ | 加入/移出日期（接口当前不返） |

### `hot_money_list` — 游资名录（v2.1 新增）

Tushare `hm_list`。约 109 个分类（赵老哥 / 章盟主 等），低频刷新（周/月级）。
`LhbFilter` 用于把龙虎榜席位识别为 `hot_money` 类型（完全相等匹配 `orgs` 字段）。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `name` | VARCHAR(100) | **PK** | 游资名称（Tushare 主键） |
| `desc` | TEXT | ✓ | 游资风格说明 |
| `orgs` | TEXT | ✓ | 关联营业部，分号/逗号分隔的原始字符串；ingest 时拆 set 用 |

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

### `lhb` — 龙虎榜汇总

Tushare `top_list`（不含席位明细）。`LhbFilter` 的 base 部分打分源。
**席位明细（top_inst）已搬到独立表 `lhb_seat_detail`**（v2.1）。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `trade_date` | DATE | **PK₁** | 交易日 |
| `ts_code` | VARCHAR(12) | **PK₂** | 股票代码 |
| `name` | VARCHAR(50) | ✓ | 股票名称 |
| `close` | FLOAT | ✓ | 当日收盘价 |
| `pct_change` | FLOAT | ✓ | 当日涨跌幅（%） |
| `turnover_rate` | FLOAT | ✓ | 换手率（%） |
| `amount` | FLOAT | ✓ | 总成交额（元，Tushare 文档说万元但实际是元） |
| `l_sell` / `l_buy` / `l_amount` | FLOAT | ✓ | 龙虎榜席位卖出/买入/总成交额（元） |
| `net_amount` | FLOAT | ✓ | 席位净买入额（元）= l_buy - l_sell |
| `net_rate` | FLOAT | ✓ | **净买入占当日总成交比例（%）**，跨股可比，LhbFilter 主输入 |
| `amount_rate` | FLOAT | ✓ | 席位成交占当日总成交比例（%），席位主导度信号 |
| `reason` | TEXT | ✓ | 上榜原因（日涨幅偏离 7% 等）；含「跌幅」整股跳过 |

**索引**：`ts_code`

**v2.1 变更**：原 `seat` JSONB 字段已 DROP（无消费方），席位明细见 `lhb_seat_detail`。

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

## 4.5. 题材增强（v2.1 新增，180 天滚动）

`ThemeFilter` 与 `sector` 平级独立维度，从三类信号合成。

### `ths_daily` — 同花顺概念/行业指数日行情

Tushare `ths_daily`。约 1232 个板块×每天一行。`ThemeFilter` 用 `pct_change` 排出题材热度 TOP N。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(20) | **PK₁** | 同花顺板块代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `name` | VARCHAR(50) | ✓ | 板块名称（冗余 `ths_index`） |
| `open` / `high` / `low` / `close` / `pre_close` / `avg_price` | FLOAT | ✓ | 板块点位 |
| `change` / `pct_change` | FLOAT | ✓ | 涨跌额 / 涨跌幅（%）；ThemeFilter 主输入 |
| `vol` / `turnover_rate` | FLOAT | ✓ | 成交量 / 换手率 |
| `total_mv` / `float_mv` | FLOAT | ✓ | 总市值 / 流通市值 |

**索引**：`(trade_date, pct_change)` → 加速 TOP N 查询

### `limit_concept_daily` — 涨停最强概念板块

Tushare `limit_cpt_list`。每日 TOP 20 涨停最强概念。`ThemeFilter` 用 `rank` 评估短线情绪。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(20) | **PK₁** | 板块代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `name` | VARCHAR(50) | ✓ | 板块名称 |
| `days` | INT | ✓ | 上榜天数 |
| `up_stat` | VARCHAR(50) | ✓ | 连板高度描述 |
| `cons_nums` / `up_nums` | INT | ✓ | 连板家数 / 涨停家数 |
| `pct_chg` | FLOAT | ✓ | 概念涨跌幅（%） |
| `rank` | INT | ✓ | 热点排名（1 最强）；ThemeFilter 主输入 |

**索引**：`(trade_date, rank)`

### `ths_concept_moneyflow` — 概念板块资金流向

Tushare `moneyflow_cnt_ths`。约 386 个概念有数据。`ThemeFilter` 资金确认信号。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `ts_code` | VARCHAR(20) | **PK₁** | 概念板块代码 |
| `trade_date` | DATE | **PK₂** | 交易日 |
| `name` | VARCHAR(50) | ✓ | 板块名称 |
| `lead_stock` | VARCHAR(50) | ✓ | 领涨股票名称 |
| `pct_change` | FLOAT | ✓ | 板块涨跌幅（%） |
| `company_num` | INT | ✓ | 成分公司数量 |
| `pct_change_stock` | FLOAT | ✓ | 领涨股涨跌幅（%） |
| `net_buy_amount` / `net_sell_amount` | FLOAT | ✓ | 买入/卖出额（亿元） |
| `net_amount` | FLOAT | ✓ | 净流入额（亿元）；> 0 触发 ThemeFilter 加分 |

**索引**：`(trade_date, net_amount)`

---

## 4.6. 龙虎榜席位明细（v2.1 新增，180 天滚动）

替代 v1 的 `lhb.seat` JSONB 字段，便于 SQL 直接过滤"今天机构净买 > N 的票"。

### `lhb_seat_detail` — 龙虎榜席位明细

Tushare `top_inst`。`LhbFilter` 的 seat 部分（base 60 + **seat 40**）打分源。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `trade_date` | DATE | **PK₁** | 交易日 |
| `ts_code` | VARCHAR(12) | **PK₂** | 股票代码 |
| `seat_key` | VARCHAR(64) | **PK₃** | **稳定席位键 = sha1(ts_code\|exalter\|side\|reason)**，避免 top_inst 重跑顺序变化导致 upsert 覆盖错行 |
| `seat_no` | INT | ✗ | 展示序号 1-N（按稳定排序生成）。**消费方排序请用 seat_no**，seat_key 顺序无业务含义 |
| `exalter` | VARCHAR(200) | ✓ | 席位/营业部名称 |
| `side` | VARCHAR(2) | ✓ | 0=买榜 / 1=卖榜（仅指榜单位置，非买卖方向） |
| `buy` / `sell` / `net_buy` | FLOAT | ✓ | 买入/卖出/净买卖金额（元） |
| `reason` | VARCHAR(100) | ✓ | 上榜原因 |
| `seat_type` | VARCHAR(20) | ✗ | ingest 时分类：`institution` / `northbound` / `hot_money` / `other` |

**索引**：`(trade_date, seat_type)`、`(trade_date, ts_code)`

### `hot_money_detail` — 游资每日交易明细

Tushare `hm_detail`（数据从 2022-08 起）。约 262 行/天。
与 `lhb_seat_detail` 互补：席位明细看金额，hm_detail 直接给"哪位游资买了什么"。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `trade_date` | DATE | **PK₁** | 交易日 |
| `ts_code` | VARCHAR(12) | **PK₂** | 股票代码 |
| `hm_name` | VARCHAR(100) | **PK₃** | 游资名称 |
| `ts_name` | VARCHAR(50) | ✓ | 股票名称 |
| `buy_amount` / `sell_amount` / `net_amount` | FLOAT | ✓ | 买入/卖出/净买卖金额（元） |
| `hm_orgs` | TEXT | ✓ | 关联营业部（从 `hot_money_list` 冗余） |
| `tag` | VARCHAR(50) | ✓ | 标签（Tushare 原始） |

**索引**：`trade_date`、`(hm_name, trade_date)`

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

> **v2.4 变更**：`filter_score_daily` / `ai_analysis` / `selection_result` 三表均新增 `strategy` 字段（`VARCHAR(20) DEFAULT 'short'`），唯一键调整为包含 `strategy`，支持短线/波段策略并行。

### `filter_score_daily` — 规则层逐维度打分

每天每股最多 **6 行**（v2.1 后：`limit`/`moneyflow`/`lhb`/`sector`/`theme`/`sentiment`）。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 评分对应的交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `strategy` | VARCHAR(20) | ✗ | 策略标识（**v2.4 新增**，server default `'short'`） |
| `dim` | VARCHAR(20) | ✗ | 维度标识，**6 选 1**（v2.1 新增 theme） |
| `score` | FLOAT | ✗ | 本维度得分 0-100 |
| `detail` | JSONB | ✓ | 打分细节 JSON，供报告/复盘 |

**唯一约束**：`(trade_date, ts_code, strategy, dim)` → `uq_filter_score_key`（v2.4 调整：加入 `strategy`）
**索引**：`(trade_date, dim)`

### `ai_analysis` — Claude AI 分析结果（Phase 3 启用）

每天每股至多 1 行。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 分析对应的交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `strategy` | VARCHAR(20) | ✗ | 策略标识（**v2.4 新增**，server default `'short'`） |
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

**唯一约束**：`(trade_date, ts_code, strategy)` → `uq_ai_analysis_key`（v2.4 调整：加入 `strategy`）

### `selection_result` — 最终选股结果

**报告和回测的主数据源**。每日全 A 股打过分的都会落库（`picked=False` 的用于复盘），其中 `rank <= top_n` 的 `picked=True`。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | INT | **PK** | 自增 |
| `trade_date` | DATE | ✗ | 选股目标交易日 |
| `ts_code` | VARCHAR(12) | ✗ | 股票代码 |
| `strategy` | VARCHAR(20) | ✗ | 策略标识（**v2.4 新增**，server default `'short'`） |
| `rank` | INT | ✗ | TOP N 排名（1 最强）；未入选填 `0` |
| `rule_score` | NUMERIC(5,2) | ✗ | 规则层综合分 0-100 |
| `ai_score` | NUMERIC(5,2) | ✓ | AI 层综合分 0-100；Phase 3 前 NULL |
| `final_score` | NUMERIC(5,2) | ✗ | 最终分（Phase 1: = rule；Phase 3: rule*0.6+ai*0.4） |
| `picked` | BOOLEAN | ✗ | 是否入选 TOP N |
| `reject_reason` | VARCHAR(200) | ✓ | 硬规则淘汰原因；入选时 NULL |
| `created_at` | TIMESTAMPTZ | ✗ | 入库时间 |

**唯一约束**：`(trade_date, ts_code, strategy)` → `uq_selection_key`（v2.4 调整：加入 `strategy`）
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

---

## swing_position（波段持仓状态）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | 自增主键 |
| mode | VARCHAR(20) | 运行模式：backtest / live |
| backtest_run_id | VARCHAR(36) | 回测批次 ID（mode=backtest 时填写） |
| trade_date | DATE | 记录日期 |
| ts_code | VARCHAR(12) | 股票代码 |
| status | VARCHAR(20) | 持仓状态：watching / holding / stopped / exited |
| entry_price | FLOAT | 入场价 |
| entry_date | DATE | 入场日期 |
| stop_loss_price | FLOAT | 当前止损价 |
| target_price | FLOAT | 目标价 |
| atr_at_entry | FLOAT | 入场时 ATR(20) |
| max_price | FLOAT | 持仓期最高价 |
| pnl_pct | FLOAT | 当前浮动盈亏 % |
| exit_reason | VARCHAR(50) | 退出原因 |
| holding_days | INT | 持仓交易日数 |
| detail | JSONB | 补充信息 |

索引：
- `(mode, trade_date)`
- `(backtest_run_id)`

查询隔离：始终带 `WHERE mode = 'backtest'` 或 `WHERE mode = 'live'`。
回测清理：`DELETE WHERE mode='backtest' AND backtest_run_id = ?`

### v2.4 schema 变更说明

Alembic migration: `alembic/versions/20260430_strategy_swing_phase0.py`

变更内容：
1. `selection_result` / `filter_score_daily` / `ai_analysis` 三表增加 `strategy` 字段（server default 'short'）
2. 历史数据回填为 'short'
3. 唯一键调整为包含 strategy
4. 新建 `swing_position` 表
