# 前端数据库数据洞察计划

## Summary

在现有 Vue 3 + FastAPI 前端中新增一个“数据洞察”入口，用来按交易日查看数据库里的原始分析数据，首版聚焦 `moneyflow` 主力资金流、`lhb` 龙虎榜汇总和 `lhb_seat_detail` 席位明细。功能只读，不接 Tushare 实时网络，不改数据库结构。

首版不做一个混合型 `daily-signals` 大查询，也不提供 `kind=all`。资金流和龙虎榜拆成两个独立汇总端点，前端用 Tab 切换或并行请求，降低 SQLite 多表 JOIN、UNION 分页和排序复杂度。

## Key Changes

- 后端新增 `src/mo_stock/web/routers/data.py`，挂载到 `/api`：
  - `GET /api/data/moneyflow-summary`
    - 参数：`trade_date`、`strategy=short|swing`、`keyword`、`sector`、`sort_by`、`order`、`page`、`page_size`
    - 返回：股票基础信息、行业、收盘涨跌、主力净流入、净流入占成交额比例、超大单/大单拆分、是否入选、规则分/最终分、资金相关维度分
    - 响应包含分页元信息和当前筛选条件下的全量聚合摘要：`total`、`page`、`page_size`、`summary.net_mf_positive_count`、`summary.total_net_mf_wan`
    - 默认按 `net_mf_ratio_pct` 降序排序；`page >= 1`，`1 <= page_size <= 100`，默认 `page_size=20`
    - 查询以 `moneyflow` 当日记录为主表，按 `ts_code + trade_date` 关联 `daily_kline` 获取 `close`、`pct_chg`、`amount`，按 `ts_code` 关联 `stock_basic` 和 `index_member` 获取名称与行业，按 `trade_date + strategy + ts_code` 关联 `selection_result` 和资金相关 `filter_score_daily`
  - `GET /api/data/lhb-summary`
    - 参数：`trade_date`、`strategy=short|swing`、`keyword`、`sector`、`sort_by`、`order`、`page`、`page_size`
    - 返回：股票基础信息、行业、收盘涨跌、龙虎榜买入/卖出/净买额、净买占比、成交占比、上榜原因、席位类型摘要、是否入选、规则分/最终分、龙虎榜相关维度分
    - 响应包含分页元信息和当前筛选条件下的全量聚合摘要：`total`、`page`、`page_size`、`summary.lhb_count`、`summary.institution_net_buy_count`、`summary.total_lhb_net_amount_wan`
    - 不直接 JOIN 明细席位行；如需席位摘要，使用按 `trade_date + ts_code` 聚合后的子查询
    - 默认按 `lhb_net_rate_pct` 降序排序；`page >= 1`，`1 <= page_size <= 100`，默认 `page_size=20`
    - 查询以 `lhb` 当日记录为主表，按 `ts_code + trade_date` 关联 `daily_kline` 获取 `close`、`pct_chg`，按 `ts_code` 关联 `stock_basic` 和 `index_member` 获取名称与行业，按 `trade_date + strategy + ts_code` 关联 `selection_result` 和龙虎榜相关 `filter_score_daily`
  - `GET /api/data/sectors`
    - 参数：`trade_date`
    - 返回：当日全市场可筛选行业列表
    - 来源：`daily_kline` 当日有行情股票 JOIN `index_member` 和 `stock_basic`，优先使用申万一级行业 `index_member.l1_name`，缺失时 fallback 到 `stock_basic.sw_l1`，最后再 fallback 到较粗的 `stock_basic.industry`；不要复用报告页只基于入选股的 `available_sectors`
  - `GET /api/data/stocks/{ts_code}/signals`
    - 参数：`end_date`、`days=20`、`strategy`；限制 `1 <= days <= 60`
    - 返回：单股近 N 日 K 线摘要、资金流序列、龙虎榜记录、维度打分 detail
    - `strategy` 只影响 `selection_result`、`filter_score_daily` 的关联结果；原始 `moneyflow/lhb` 数据本身不按策略隔离
  - `GET /api/data/stocks/{ts_code}/lhb-seats`
    - 参数：`trade_date`
    - 返回：龙虎榜席位明细，按 `seat_no` 排序，包含席位名、买入、卖出、净买、席位类型、上榜原因

- 后端新增 Pydantic schema：
  - 新增 schema 统一放在现有 `src/mo_stock/web/schemas.py`，不新建 schema 文件
  - 所有金额统一输出展示友好的字段，例如 `net_mf_wan`、`lhb_net_amount_wan`
  - `moneyflow.net_mf_amount` 按万元输出
  - `daily_kline.amount` 从千元换算成交额，用于计算 `net_mf_ratio_pct = 1000 * net_mf_wan / amount`
  - `lhb.net_amount/l_buy/l_sell/l_amount/amount` 从元换算到万元展示
  - 缺失数据返回 `null`，前端显示“暂无”，不把缺失误判为 0
  - 比例类字段遇到分子缺失、分母缺失或分母为 0 时返回 `null`，避免除零或把缺失数据渲染成 0
  - 维度分字段用可选值或 `scores` map 表示；不要假设 `swing` 策略一定有 `moneyflow`/`lhb` 维度，`swing` 可能对应 `moneyflow_swing` 或 `catalyst`
  - summary 响应结构建议统一为 `{ items, total, page, page_size, summary }`，便于前端概览区和分页控件直接使用

- 参数校验与查询安全：
  - `strategy` 使用 `Literal["short", "swing"]` 或等价白名单
  - `order` 限制为 `asc|desc`
  - `sort_by` 使用类似 `reports.py` 的 `VALID_SORT_BY` 映射字典转换为 SQLAlchemy 列或表达式；不要把用户输入直接传入 `order_by()`
  - `moneyflow-summary` 建议支持的 `sort_by`：`net_mf_ratio_pct`、`net_mf_wan`、`pct_chg`、`final_score`、`rule_score`
  - `lhb-summary` 建议支持的 `sort_by`：`lhb_net_rate_pct`、`lhb_net_amount_wan`、`pct_chg`、`final_score`、`rule_score`
  - `sector` 过滤使用与展示行业相同的 fallback 口径：`index_member.l1_name`、`stock_basic.sw_l1`、`stock_basic.industry`

- 前端新增页面 `/data`：`frontend/src/views/DataInsight.vue`
  - 顶部筛选：交易日、策略、数据类型 Tab、行业、股票代码/名称搜索
  - 页面初始化时请求 `/api/data/sectors` 获取行业下拉
  - 概览指标：主力净流入股票数、合计主力净流入、龙虎榜上榜数、机构净买上榜数
  - 资金流 Tab 请求 `moneyflow-summary`，列表卡片按资金净流入占比、净流入额、最终分排序
  - 龙虎榜 Tab 请求 `lhb-summary`，列表卡片按龙虎榜净买占比、净买额、最终分排序
  - 展开资金流卡片只展示当前响应已有字段：超大单/大单买卖、资金维度分、规则分/最终分
  - 展开龙虎榜卡片展示当前响应已有字段：上榜原因、净买占比、成交占比、席位类型摘要
  - 完整席位明细只在用户点击“查看席位明细”按钮后请求 `lhb-seats`，不在卡片展开时自动请求

- 个股详情页增强：
  - 在 `StockDetail.vue` 增加“数据明细”折叠面板或 Tab，避免拉长现有页面
  - 展示近 20 日资金流时间线和龙虎榜上榜记录
  - 保留现有 AI 分析、维度评分、近期入选记录，不改变原页面主流程

- 前端路由入口：
  - 在 `frontend/src/router/index.ts` 注册 `/data`
  - 在 `ReportList.vue` 顶部导航右侧增加“数据洞察”入口，与现有“执行”入口并列，避免新页面不可发现

## Test Plan

- 后端测试：
  - SQLite 内存库构造 `Moneyflow`、`Lhb`、`LhbSeatDetail`、`DailyKline`、`SelectionResult`、`FilterScoreDaily`、`StockBasic`、`IndexMember` 数据
  - 覆盖 `moneyflow-summary` 的分页、排序、关键词搜索、行业过滤、空数据日
  - 覆盖 `lhb-summary` 的分页、排序、关键词搜索、行业过滤、空数据日
  - 覆盖分页边界：`page` 超出范围、`page_size=0`、`page_size>100`
  - 精确断言单位换算和 `net_mf_ratio_pct`
  - 精确断言 `lhb.net_amount/l_buy/l_sell/l_amount/amount` 从元换算到万元
  - 断言比例字段在分母为 0 或缺失时返回 `null`
  - 断言席位明细按 `seat_no` 排序
  - 断言 `lhb-summary` 不因一股多席位产生重复股票行
  - 断言 `moneyflow-summary` 和 `lhb-summary` 响应包含 `total`、`page`、`page_size` 和 `summary` 聚合字段，且聚合基于筛选后的全量结果，不受当前分页限制
  - 断言 `/api/data/sectors` 返回当日全市场行业，而不是只返回入选股行业；行业优先级为 `index_member.l1_name`、`stock_basic.sw_l1`、`stock_basic.industry`
  - 断言 `/api/data/stocks/{ts_code}/signals` 的 `days` 参数限制在 `1..60`
  - 断言非法参数返回 422 或 400

- 前端验证：
  - 更新 `frontend/src/api/index.ts` 类型和请求方法
  - 更新 `frontend/src/router/index.ts` 注册 `/data`，并在 `ReportList.vue` 顶部导航增加可访问入口
  - `npm run build` 通过 TypeScript 类型检查
  - 本地联调 `/data`、`/stock/:code`，确认空数据、部分缺失数据、完整数据三类状态展示正常
  - 验证概览指标来自 summary 响应，不受当前页只返回 20 条数据影响
  - 验证切换 Tab、行业筛选、关键词搜索、分页、排序不会互相污染状态
  - 验证席位明细只在点击按钮后请求，快速滑动或展开卡片不会触发大量请求

## Assumptions

- 首版只做只读数据查看，不做数据编辑、手动刷新、Tushare 实时拉取。
- 不新增数据库表和迁移，直接复用现有 `moneyflow`、`lhb`、`lhb_seat_detail`、`daily_kline`、`filter_score_daily`、`selection_result`。
- `/data` 页面偏移动端布局，继续使用现有 Vant + Tailwind 风格。
- `strategy` 只用于关联选股结果和维度分；原始 `moneyflow/lhb` 数据本身不按策略隔离。
- 首版不实现 `kind=all`。如果后续需要统一列表，再用 `moneyflow` 与 `lhb` 的股票代码 UNION 作为候选集，并在响应中显式返回 `has_moneyflow`、`has_lhb` 或 `sources`。
