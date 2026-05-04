# mo-stock 前端说明

`frontend/` 是 mo-stock-selection 的 Web 前端项目，基于 Vue 3 + TypeScript + Vite 构建，用于查看选股报告、筛选个股、查看 AI 分析，以及手动触发选股任务和调度器。

前端和后端分开启动：前端由 Vite 提供开发服务，后端由 FastAPI 提供 `/api` 接口。开发环境中，Vite 会把 `/api` 请求代理到 `http://localhost:8000`。

## 技术栈

- Vue 3：页面与组件开发
- TypeScript：类型约束
- Vue Router：前端路由
- Vant 4：移动端 UI 组件
- Tailwind CSS v4：工具类样式
- Axios：HTTP API 调用
- Vite：开发服务器和生产构建

## 目录结构

```text
frontend/
├── src/
│   ├── api/index.ts              # API 客户端、接口类型、维度标签
│   ├── auth.ts                   # Basic Auth 会话读写
│   ├── router/index.ts           # 路由与登录守卫
│   ├── components/               # 通用展示组件
│   │   ├── AiSummary.vue
│   │   ├── DimensionBar.vue
│   │   ├── MarketOverview.vue
│   │   └── ScoreTable.vue
│   ├── views/                    # 页面组件
│   │   ├── Login.vue             # 登录页
│   │   ├── ReportList.vue        # 报告列表首页
│   │   ├── ReportDetail.vue      # 单日报告详情
│   │   ├── StockDetail.vue       # 个股详情
│   │   └── Execute.vue           # 手动执行 / 定时调度
│   ├── App.vue                   # 根组件
│   ├── main.ts                   # 应用入口
│   └── style.css                 # 全局样式入口
├── vite.config.ts                # Vite 配置和 /api 代理
├── package.json                  # npm 脚本和依赖
└── index.html                    # HTML 入口
```

## 本地启动

先启动后端 API。后端当前没有单独的 `mo-stock web` 命令，直接用 `uvicorn` 启动 FastAPI：

```bash
# 项目根目录
.venv/bin/uvicorn mo_stock.web.app:app --reload --host 0.0.0.0 --port 8000
```

再启动前端：

```bash
cd frontend
npm install
npm run dev
```

Vite 默认会输出访问地址，通常是：

```text
http://localhost:5173
```

开发代理配置在 `vite.config.ts`：

```ts
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    },
  },
}
```

因此前端 API 客户端只需要使用相对路径：

```ts
baseURL: '/api'
```

## 常用命令

```bash
# 安装依赖
npm install

# 启动开发服务
npm run dev

# 类型检查并构建生产包
npm run build

# 本地预览 dist 构建结果
npm run preview
```

## 页面与路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/login` | `Login.vue` | Basic Auth 登录页 |
| `/` | `ReportList.vue` | 按 short / swing 策略查看历史报告 |
| `/report/:date?strategy=short` | `ReportDetail.vue` | 查看指定日期的选股报告，支持搜索、行业筛选、排序 |
| `/stock/:code?strategy=short` | `StockDetail.vue` | 查看个股维度评分、AI 分析和近期入选记录 |
| `/execute` | `Execute.vue` | 手动触发选股任务、启动或停止定时调度 |

除 `/login` 外，其余页面都需要登录。登录状态保存在 `localStorage` 或 `sessionStorage`，由 `src/auth.ts` 统一管理。

## API 对接

前端接口统一封装在 `src/api/index.ts`，主要调用：

| 前端方法 | 后端接口 | 用途 |
|----------|----------|------|
| `verifyAuth` | `GET /api/health` | 登录时校验 Basic Auth |
| `fetchReports` | `GET /api/reports` | 获取报告列表 |
| `fetchReportDetail` | `GET /api/reports/{date}` | 获取单日报告详情 |
| `fetchStockDetail` | `GET /api/stocks/{ts_code}` | 获取个股详情 |
| `runTask` | `POST /api/tasks/run` | 手动触发一次选股 |
| `fetchTaskStatus` | `GET /api/tasks/status` | 查询当前任务状态 |
| `startScheduler` | `POST /api/scheduler/start` | 启动调度器 |
| `stopScheduler` | `POST /api/scheduler/stop` | 停止调度器 |
| `fetchSchedulerStatus` | `GET /api/scheduler/status` | 查询调度器状态 |

请求拦截器会自动从本地会话中读取 `Authorization` 并添加到请求头；响应拦截器遇到 `401` 会清理会话并跳转登录页。

## 构建产物

`npm run build` 会生成 `frontend/dist/`，用于生产环境静态部署。生产部署时通常由 Nginx 或其他静态服务器托管 `dist`，同时把 `/api` 反向代理到 FastAPI 后端。

## 开发注意事项

- 前端代码目前偏移动端布局，主要使用 Vant 组件。
- 新增 API 字段时，先同步更新 `src/api/index.ts` 中的 TypeScript 类型。
- 新增页面时，需要在 `src/router/index.ts` 添加路由，并按需配置 `meta.requiresAuth`。
- 选股维度标签统一维护在 `DIM_LABELS`，不要在页面里重复硬编码。
- 提交前建议执行 `npm run build`，确认 `vue-tsc` 类型检查和 Vite 构建都通过。
