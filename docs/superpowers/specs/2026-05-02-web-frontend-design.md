# mo-stock Web 前端设计文档

> 日期：2026-05-02
> 状态：待审核

## 目标

为 mo-stock-selection 选股系统增加 Web 前端，解决三个核心痛点：
1. **历史翻阅** — 按日期浏览所有选股报告
2. **交互筛选** — 按分数、行业、维度排序筛选
3. **手机查看** — 部署到 VPS，手机浏览器直接用

## 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 后端 | FastAPI + SQLAlchemy | 复用现有数据层 + PostgreSQL |
| 前端 | Vue 3 + TailAdmin Stock 模板 + Tailwind CSS | 免费开源，自带股票类组件，500+ UI 组件 |
| 图表 Phase 1 | CSS 条形图 | 维度分用纯 CSS 条形图，零依赖 |
| 图表 Phase 2 | ECharts | 雷达图、饼图等复杂图表 |
| 部署 | Docker Compose 扩展现有 PG + 新增 api / nginx | VPS 一键部署 |

## 架构

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────┐
│   Vue 3 SPA  │────▶│   FastAPI 后端    │────▶│ PostgreSQL  │
│ (TailAdmin)  │◀────│ (REST API)       │     │ (现有 PG)   │
└──────────────┘     └──────────────────┘     └─────────────┘
```

本地开发可用 SQLite fixture，生产走现有 PostgreSQL（docker-compose.yml 中的 pg 服务）。

### 目录结构

```
mo-stock-selection/
├── src/mo_stock/
│   ├── web/                        # 新增 Web 模块
│   │   ├── __init__.py
│   │   ├── app.py                  # FastAPI 应用入口
│   │   ├── schemas.py              # Pydantic response schema
│   │   └── routers/
│   │       ├── reports.py          # 报告列表 + 详情 API
│   │       └── stocks.py           # 单股详情 API
│   ├── storage/                    # 现有，不动
│   └── ...
├── frontend/                       # 新增前端项目
│   ├── src/
│   │   ├── views/
│   │   │   ├── ReportList.vue      # 报告列表页
│   │   │   ├── ReportDetail.vue    # 当日选股详情页
│   │   │   └── StockDetail.vue     # 单股详情页
│   │   ├── components/             # 可复用组件
│   │   │   ├── ScoreTable.vue      # 选股结果表格
│   │   │   ├── DimensionBar.vue    # 维度分 CSS 条形图（Phase 1）
│   │   │   ├── MarketOverview.vue  # 大盘概览卡片
│   │   │   └── AiSummary.vue       # AI 分析摘要
│   │   ├── router/index.ts
│   │   └── api/index.ts            # axios 封装
│   ├── package.json
│   └── vite.config.ts
├── Dockerfile                      # 新增（FastAPI 应用镜像）
├── .dockerignore                   # 新增
├── docker-compose.yml              # 扩展现有：新增 api + nginx 服务
└── nginx.conf                      # 新增
```

## 前端构建流程

```bash
# 1. 构建前端静态文件
cd frontend && npm install && npm run build   # 产出 frontend/dist/

# 2. 启动所有服务
docker compose up -d                           # Nginx 挂载 frontend/dist/
```

`frontend/dist/` 必须在 `docker compose up` 前存在，否则 Nginx 挂载空目录。

本地开发前端用 Vite proxy 转发 `/api` 到 FastAPI :8000，不走 Nginx，不加 CORS。FastAPI 路由统一挂载 `/api` 前缀，与 nginx `location /api/` 对齐。

## API 设计

### 参数校验规则

所有 API 参数使用白名单校验，非法值返回 `400 Bad Request`：

| 参数 | 合法值 | 默认 |
|------|--------|------|
| `strategy` | `short` \| `swing` | `short` |
| `sort_by` | `final_score` \| `rule_score` \| `ai_score` \| `limit` \| `moneyflow` \| `lhb` \| `sector` \| `theme` \| `trend` \| `pullback` \| `moneyflow_swing` \| `sector_swing` \| `theme_swing` \| `catalyst` \| `risk_liquidity` | `final_score` |
| `order` | `asc` \| `desc` | `desc` |
| `page` | 正整数 | 1 |
| `page_size` | 1-100 | 20 |

`sort_by` 后端使用映射表转换为实际字段，禁止动态拼接字符串。跨策略行为：当前策略不存在的维度按 0 排序（不返回 400），前端实现更简单。

### 报告列表 `GET /api/reports`

查询参数：`strategy`, `page`, `page_size`

响应：
```json
{
  "items": [
    {
      "trade_date": "2026-04-30",
      "strategy": "short",
      "count": 12,
      "avg_score": 72.3,
      "max_score": 85.2
    }
  ],
  "total": 45,
  "page": 1,
  "page_size": 20
}
```

`total` 为交易日数量（只统计有 `picked=true` 的日期），`count` 为当日入选数。

### 报告详情 `GET /api/reports/{date}`

查询参数：`strategy`, `sort_by`, `order`, `sector`, `keyword`

响应：
```json
{
  "trade_date": "2026-04-30",
  "strategy": "short",
  "market": {
    "sh_index": { "close": 3245.0, "pct_chg": 0.8 },
    "hs300_index": { "close": 3876.0, "pct_chg": 0.9 },
    "regime_score": 72
  },
  "stocks": [
    {
      "rank": 1,
      "ts_code": "600519.SH",
      "name": "贵州茅台",
      "industry": "食品饮料",
      "final_score": 85.2,
      "rule_score": 82.0,
      "ai_score": 90.0,
      "scores": {
        "limit": 92,
        "moneyflow": 85,
        "lhb": 78,
        "sector": 70,
        "theme": 65
      },
      "ai_summary": "该股受白酒板块资金回流驱动...",
      "picked": true
    }
  ],
  "available_sectors": ["食品饮料", "电气设备", "医药生物", "..."]
}
```

`picked` 当前 Phase 1 只返回 `true`（入选股），保留字段便于 Phase 2 扩展候选池视图。
```

#### 市场数据口径

| 字段 | 数据来源 | 说明 |
|------|----------|------|
| `sh_index` | `daily_kline` where `ts_code = '000001.SH'` | 上证综指 |
| `hs300_index` | `daily_kline` where `ts_code = '000300.SH'` | 沪深 300（对齐现有 market_regime） |
| `regime_score` | `MarketRegimeFilter().score_market(session, trade_date)` 实时重算 | 非历史快照；未来如需历史一致性再持久化 |

#### 行业口径

`industry` 展示和 `sector` 筛选统一使用申万一级 `index_member.l1_name`，而非 `stock_basic.industry`。

#### 维度分单位

- 维度分（`scores` 下各维度）：0-100 取整
- 综合分（`final_score` / `rule_score` / `ai_score`）：0-100 保留 1 位小数

### 单股详情 `GET /api/stocks/{ts_code}`

查询参数：`strategy`, `days`

响应：
```json
{
  "ts_code": "600519.SH",
  "name": "贵州茅台",
  "industry": "食品饮料",
  "latest_scores": {
    "limit": 92,
    "moneyflow": 85,
    "lhb": 78,
    "sector": 70,
    "theme": 65
  },
  "ai_analysis": {
    "thesis": "该股受白酒板块资金回流驱动...",
    "key_catalysts": ["年报业绩超预期", "北向资金连续净买入"],
    "risks": ["白酒消费降级风险", "估值偏高"],
    "suggested_entry": "1850-1870 元",
    "stop_loss": "跌破 1800 元止损"
  },
  "recent_picks": [
    { "trade_date": "2026-04-30", "picked": true, "final_score": 85.2 },
    { "trade_date": "2026-04-29", "picked": true, "final_score": 78.1 }
  ]
}
```

`ai_analysis` 字段直接对齐 ORM `AiAnalysis` 模型：`thesis`, `key_catalysts`, `risks`, `suggested_entry`, `stop_loss`。

报告详情中的 `ai_summary` 是 `thesis` 截断后前 100 字符 + "..."，用于表格内摘要展示。

空状态：AI 缺失时 `ai_analysis` 返回 `null`，前端优雅降级。

### 健康检查 `GET /api/health`

非业务 API，用于 Docker/Nginx 健康检查。返回 `{"status": "ok"}`，检查数据库连通性。

### 导出 Markdown `GET /api/reports/{date}/export`（Phase 2）

返回当日报告的 Markdown 文件（复用现有 `render_md.py` 逻辑）。Phase 2 实现。

## 页面设计

### 页面 1：报告列表（首页 `/`）

简洁表格，按日期倒序：
- 策略切换 tabs（短线 / 波段）
- 每行：日期、入选数、平均分、最高分、[查看] 按钮
- 底部分页
- 空状态：无报告时提示"暂无选股数据，请先运行 run-once"

### 页面 2：当日选股详情（`/report/:date`）

- 顶部：大盘概览卡片（上证/沪深 300 涨跌 + regime 分数）
- 主体：入选股票表格，支持排序（综合分、各维度分）、筛选（行业下拉）、搜索（名称/代码）
- 点击行展开：维度分 CSS 条形图 + AI 分析摘要
- 右上角：[导出 Markdown] 按钮（Phase 2）
- 空状态：当日无入选时提示"当日无入选股票"

### 页面 3：单股详情（`/stock/:code`）

- 维度打分明细（CSS 条形图，Phase 1）
- AI 深度分析全文（thesis + key_catalysts + risks + suggested_entry + stop_loss）
- 近期选股记录时间线
- 空状态：AI 缺失时不渲染 AI 区域

### 不做的事

- 不做 K 线图（同花顺更好）
- 不做实时行情（选股系统收盘后运行）
- 不做用户登录系统（Nginx Basic Auth 即可）
- 不做回测可视化（CLI 跑回测）
- 不做自动交易

## 部署方案

### Dockerfile

基于 `python:3.12-slim`，安装依赖 + 复制代码。

### .dockerignore

排除：`.venv`, `.git`, `data`, `frontend/node_modules`, `frontend/dist`, `__pycache__`, `*.pyc`

### docker-compose.yml 扩展

保留现有 pg 服务，新增 api 和 nginx 服务：

```yaml
# docker-compose.yml — 扩展现有
services:
  pg:
    # ... 保持不变

  api:
    build:
      context: .
      dockerfile: Dockerfile
    command: uvicorn mo_stock.web.app:app --host 0.0.0.0 --port 8000
    environment:
      - DB_URL=postgresql+psycopg2://mo_stock:mo_stock@pg:5432/mo_stock
    depends_on:
      pg:
        condition: service_healthy
    ports:
      - "8000:8000"

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - .htpasswd:/etc/nginx/.htpasswd:ro
    depends_on:
      - api
```

### Nginx 配置

```nginx
server {
    listen 80;
    auth_basic "mo-stock";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # SPA fallback：所有非文件请求回退到 index.html
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 数据库初始化

容器启动前需要数据库已有表结构。本项目 Web 容器不自动建表。部署流程：

```bash
# 确保 PG 表结构最新
# 宿主机已配置 Python 环境时：
alembic upgrade head
# 或首次部署：
mo-stock init-db

# VPS Docker 部署时：
docker compose run --rm api alembic upgrade head
# 或首次部署：
docker compose run --rm api mo-stock init-db
```

## 后端依赖

`pyproject.toml` 新增：
```
"fastapi>=0.110",
"uvicorn[standard]>=0.29",
```

## 开发分期

### Phase 1（MVP，优先交付）
- `pyproject.toml` 新增 fastapi / uvicorn 依赖
- FastAPI 后端 3 个 API 端点（报告列表、报告详情、单股详情）
- Pydantic response schema，不直接返回 ORM
- Vue 前端 3 个页面（报告列表、当日详情、单股详情）
- Phase 1 图表用 CSS 条形图，不引入 ECharts
- Dockerfile + .dockerignore + 扩展 docker-compose.yml + nginx.conf（含 SPA fallback）
- Nginx Basic Auth（Phase 1 即包含），创建密码文件：`htpasswd -c .htpasswd <username>`（需安装 htpasswd 工具：macOS `brew install httpd`，Linux `apt install apache2-utils`）
- 预计工作量：2-3 天

### Phase 2（增强，后续迭代）
- ECharts 图表（维度雷达图、板块分布饼图）
- 导出 Markdown API + 按钮
- 多日对比视图
