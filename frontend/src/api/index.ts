import axios, { AxiosHeaders } from 'axios'
import { clearAuthSession, getAuthSession } from '../auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

/**
 * HTTP 请求拦截器：请求前添加 Authorization 头
 * 从本地存储读取认证会话信息，若存在则附加到请求头中
 */
api.interceptors.request.use((config) => {
  const auth = getAuthSession()
  const headers = AxiosHeaders.from(config.headers)
  if (auth && !headers.has('Authorization')) {
    headers.set('Authorization', auth.authorization)
  }
  config.headers = headers
  return config
})

/**
 * HTTP 响应拦截器：统一处理 401 未授权
 * 清除本地认证会话，并重定向到登录页（保留当前页面路径作为 redirect 参数）
 */
api.interceptors.response.use(
  response => response,
  error => {
    if (error?.response?.status === 401) {
      clearAuthSession()
      if (window.location.pathname !== '/login') {
        const redirect = `${window.location.pathname}${window.location.search}`
        window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
      }
    }
    return Promise.reject(error)
  },
)

// ============================ 维度中英文映射 ============================
// 用于在界面上显示维度名称时，同时展示英文 key 和中文标签

/**
 * 维度标签映射表
 * - short 策略：limit（涨停异动）、moneyflow（资金流向）、lhb（龙虎榜）、sector（板块）、theme（题材）
 * - swing 策略：trend（趋势结构）、pullback（回踩承接）、moneyflow_swing（波段资金）、
 *   sector_swing（行业持续）、theme_swing（题材持续）、catalyst（短线催化）、risk_liquidity（风险流动性）
 */
export const DIM_LABELS: Record<string, string> = {
  // short 维度
  limit: '涨停异动',
  moneyflow: '资金流向',
  lhb: '龙虎榜',
  sector: '板块',
  theme: '题材',
  // swing 维度
  trend: '趋势结构',
  pullback: '回踩承接',
  moneyflow_swing: '波段资金',
  sector_swing: '行业持续',
  theme_swing: '题材持续',
  catalyst: '短线催化',
  risk_liquidity: '风险流动性',
}

/**
 * 获取维度的中文标签，格式为"中文名 (英文key)"
 * @param key 维度英文标识符
 * @returns 中文标签字符串，格式如"涨停异动 (limit)"
 */
export function dimLabel(key: string): string {
  const zh = DIM_LABELS[key]
  return zh ? `${zh} (${key})` : key
}

// ============================ TypeScript 类型定义 ============================

/**
 * 报告列表项：按日期聚合的选股报告摘要
 */
export interface ReportListItem {
  trade_date: string           // 交易日期，格式 YYYY-MM-DD
  strategy: string              // 策略类型：'short' 或 'swing'
  count: number                // 入选股票数量
  avg_score: number            // 平均综合分
  max_score: number            // 最高综合分
}

/**
 * 报告列表响应结构：包含分页信息
 */
export interface ReportListResponse {
  items: ReportListItem[]      // 报告列表
  total: number                // 总记录数
  page: number                 // 当前页码
  page_size: number            // 每页大小
}

/**
 * 指数快照：包含收盘价和涨跌幅
 */
export interface IndexSnapshot {
  close: number                 // 收盘点位
  pct_chg: number               // 涨跌幅百分比
}

/**
 * 市场数据：包含主要指数和大盘环境评分
 */
export interface MarketData {
  sh_index: IndexSnapshot       // 上证指数快照
  hs300_index: IndexSnapshot    // 沪深 300 指数快照
  regime_score: number          // 大盘环境评分（主要用于 swing 策略组合层控制）
}

/**
 * 股票列表项：单只股票的评分和选股结果
 */
export interface StockItem {
  rank: number                  // 排名（按综合分排序）
  ts_code: string               // 股票代码，如 600519.SH
  name: string                  // 股票名称
  industry: string              // 所属行业（申万一级）
  final_score: number           // 最终综合分；AI 缺失时等于 rule_score，否则按配置权重融合
  rule_score: number            // 规则维度综合分
  ai_score: number | null        // AI 评分（可能为 null 尚未分析）
  scores: Record<string, number> // 各维度原始得分
  ai_summary: string | null     // AI 论点摘要（由后端截断生成）
  picked: boolean               // 是否进入当日报告入选列表
}

/**
 * 报告详情响应结构：包含市场数据和股票列表
 */
export interface ReportDetailResponse {
  trade_date: string            // 交易日期
  strategy: string               // 策略类型
  market: MarketData             // 当日市场数据
  stocks: StockItem[]            // 入选股票列表
  available_sectors: string[]   // 当前可选行业过滤器列表
}

/**
 * AI 分析数据：个股深度分析结果
 */
export interface AiAnalysisData {
  thesis: string                // AI 核心论点/选股逻辑
  key_catalysts: string[] | null // 关键催化剂/利好因素
  risks: string[] | null         // 风险因素
  suggested_entry: string | null // 建议入场价位
  stop_loss: string | null       // 止损价位
}

/**
 * 最近选中记录：历史选股结果追踪
 */
export interface RecentPick {
  trade_date: string            // 交易日期
  picked: boolean               // 是否被选中
  final_score: number           // 当日综合分
}

/**
 * 股票详情响应结构：包含评分、AI 分析和近期表现
 */
export interface StockDetailResponse {
  ts_code: string               // 股票代码
  name: string                  // 股票名称
  industry: string              // 所属行业
  latest_scores: Record<string, number>  // 各维度最新得分
  ai_analysis: AiAnalysisData | null     // AI 深度分析结果
  recent_picks: RecentPick[]    // 最近 N 天该股的选股记录
}

export interface MoneyflowSummaryStats {
  net_mf_positive_count: number
  total_net_mf_wan: number | null
}

export interface MoneyflowSummaryItem {
  ts_code: string
  name: string
  industry: string | null
  close: number | null
  pct_chg: number | null
  net_mf_wan: number | null
  net_mf_ratio_pct: number | null
  buy_lg_wan: number | null
  sell_lg_wan: number | null
  buy_elg_wan: number | null
  sell_elg_wan: number | null
  picked: boolean
  rule_score: number | null
  final_score: number | null
  scores: Record<string, number>
}

export interface MoneyflowSummaryResponse {
  items: MoneyflowSummaryItem[]
  total: number
  page: number
  page_size: number
  summary: MoneyflowSummaryStats
}

export interface LhbSummaryStats {
  lhb_count: number
  institution_net_buy_count: number
  total_lhb_net_amount_wan: number | null
}

export interface LhbSummaryItem {
  ts_code: string
  name: string
  industry: string | null
  close: number | null
  pct_chg: number | null
  lhb_buy_wan: number | null
  lhb_sell_wan: number | null
  lhb_amount_wan: number | null
  lhb_net_amount_wan: number | null
  lhb_net_rate_pct: number | null
  lhb_amount_rate_pct: number | null
  reason: string | null
  seat_summary: Record<string, number>
  picked: boolean
  rule_score: number | null
  final_score: number | null
  scores: Record<string, number>
}

export interface LhbSummaryResponse {
  items: LhbSummaryItem[]
  total: number
  page: number
  page_size: number
  summary: LhbSummaryStats
}

export interface SectorListResponse {
  trade_date: string
  sectors: string[]
}

export interface StockKlineSignal {
  trade_date: string
  close: number | null
  pct_chg: number | null
  amount: number | null
}

export interface StockMoneyflowSignal {
  trade_date: string
  net_mf_wan: number | null
  net_mf_ratio_pct: number | null
  buy_lg_wan: number | null
  sell_lg_wan: number | null
  buy_elg_wan: number | null
  sell_elg_wan: number | null
}

export interface StockLhbSignal {
  trade_date: string
  lhb_net_amount_wan: number | null
  lhb_net_rate_pct: number | null
  reason: string | null
}

export interface StockScoreSignal {
  trade_date: string
  dim: string
  score: number
  detail: Record<string, any> | null
}

export interface StockSelectionSignal {
  trade_date: string
  picked: boolean
  rule_score: number | null
  final_score: number | null
}

export interface StockSignalsResponse {
  ts_code: string
  name: string | null
  industry: string | null
  kline: StockKlineSignal[]
  moneyflow: StockMoneyflowSignal[]
  lhb: StockLhbSignal[]
  scores: StockScoreSignal[]
  selections: StockSelectionSignal[]
}

export interface LhbSeatItem {
  seat_no: number
  exalter: string | null
  side: string | null
  buy_wan: number | null
  sell_wan: number | null
  net_buy_wan: number | null
  seat_type: string
  reason: string | null
}

export interface LhbSeatsResponse {
  trade_date: string
  ts_code: string
  seats: LhbSeatItem[]
}

// ============================ API 请求方法 ============================

/**
 * 验证认证信息是否有效（登录时使用）
 * 生产部署下 /api 由 FastAPI Basic Auth 保护；本地未配置密码时 /health 仅检查服务可用性。
 * @param authorization Basic Auth 字符串
 */
export function verifyAuth(authorization: string) {
  return api.get<{ status: string }>('/health', {
    headers: { Authorization: authorization },
  })
}

/**
 * 获取报告列表（分页）
 * @param strategy 策略类型：'short' 或 'swing'
 * @param page 页码（从 1 开始）
 * @param pageSize 每页记录数
 */
export function fetchReports(strategy: string, page: number, pageSize: number) {
  return api.get<ReportListResponse>('/reports', {
    params: { strategy, page, page_size: pageSize },
  })
}

/**
 * 获取报告详情（股票列表）
 * @param date 交易日期 YYYY-MM-DD
 * @param strategy 策略类型
 * @param sortBy 排序字段：'final_score'、'rule_score'、'ai_score'
 * @param order 排序方向：'desc' 或 'asc'
 * @param sector 行业过滤器（可选）
 * @param keyword 股票名称/代码关键字搜索（可选）
 */
export function fetchReportDetail(
  date: string,
  strategy: string,
  sortBy = 'final_score',
  order = 'desc',
  sector?: string,
  keyword?: string,
) {
  return api.get<ReportDetailResponse>(`/reports/${date}`, {
    params: { strategy, sort_by: sortBy, order, sector, keyword },
  })
}

/**
 * 获取个股详情：评分明细、AI 分析、历史选股记录
 * @param tsCode 股票代码，如 600519.SH
 * @param strategy 策略类型
 * @param days 查询最近 N 天的历史数据
 */
export function fetchStockDetail(tsCode: string, strategy: string, days = 10) {
  return api.get<StockDetailResponse>(`/stocks/${tsCode}`, {
    params: { strategy, days },
  })
}

export function fetchDataSectors(tradeDate: string) {
  return api.get<SectorListResponse>('/data/sectors', {
    params: { trade_date: tradeDate },
  })
}

export function fetchMoneyflowSummary(params: {
  tradeDate: string
  strategy: string
  keyword?: string
  sector?: string
  sortBy?: string
  order?: 'asc' | 'desc'
  page?: number
  pageSize?: number
}) {
  return api.get<MoneyflowSummaryResponse>('/data/moneyflow-summary', {
    params: {
      trade_date: params.tradeDate,
      strategy: params.strategy,
      keyword: params.keyword,
      sector: params.sector,
      sort_by: params.sortBy,
      order: params.order,
      page: params.page,
      page_size: params.pageSize,
    },
  })
}

export function fetchLhbSummary(params: {
  tradeDate: string
  strategy: string
  keyword?: string
  sector?: string
  sortBy?: string
  order?: 'asc' | 'desc'
  page?: number
  pageSize?: number
}) {
  return api.get<LhbSummaryResponse>('/data/lhb-summary', {
    params: {
      trade_date: params.tradeDate,
      strategy: params.strategy,
      keyword: params.keyword,
      sector: params.sector,
      sort_by: params.sortBy,
      order: params.order,
      page: params.page,
      page_size: params.pageSize,
    },
  })
}

export function fetchStockSignals(tsCode: string, endDate: string, strategy: string, days = 20) {
  return api.get<StockSignalsResponse>(`/data/stocks/${tsCode}/signals`, {
    params: { end_date: endDate, strategy, days },
  })
}

export function fetchLhbSeats(tsCode: string, tradeDate: string) {
  return api.get<LhbSeatsResponse>(`/data/stocks/${tsCode}/lhb-seats`, {
    params: { trade_date: tradeDate },
  })
}

// ============================ 任务执行相关类型和方法 ============================

/**
 * 任务状态响应：一次性选股任务的执行状态
 */
export interface TaskStatusResponse {
  task_id: string | null       // 任务 ID（运行中有值）
  status: 'running' | 'idle' | 'error'  // 运行中 / 空闲 / 错误
  strategy: string | null       // 当前策略类型
  trade_date: string | null     // 当前任务针对的交易日期
  started_at: string | null     // 任务开始时间
  error: string | null          // 错误信息（如有）
}

/**
 * 定时任务状态响应：调度器的运行状态
 */
export interface SchedulerStatusResponse {
  status: 'running' | 'stopped'  // 运行中 / 已停止
  strategy: string | null          // 调度中的策略类型
  cron: string | null             // Cron 表达式
  next_run: string | null          // 下次执行时间
}

/**
 * 触发一次性选股任务执行
 * @param strategy 策略类型：'short' 或 'swing'
 * @param tradeDate 指定交易日期（可选，不传时后端默认当天）
 * @param skipAi 是否跳过 AI 分析阶段
 */
export function runTask(strategy: string, tradeDate?: string, skipAi = false) {
  return api.post<TaskStatusResponse>('/tasks/run', {
    strategy,
    trade_date: tradeDate || null,
    skip_ai: skipAi,
  })
}

/**
 * 查询当前任务状态（轮询用）
 */
export function fetchTaskStatus() {
  return api.get<TaskStatusResponse>('/tasks/status')
}

/**
 * 启动定时调度器（每日收盘后自动执行选股）
 * @param strategy 策略类型
 * @param skipAi 是否跳过 AI 分析
 * @param cronHour 定时执行小时（默认 15，即下午 3 点）
 * @param cronMinute 定时执行分钟（默认 30）
 */
export function startScheduler(strategy: string, skipAi = false, cronHour = 15, cronMinute = 30) {
  return api.post<{ message: string; cron: string }>('/scheduler/start', {
    strategy,
    skip_ai: skipAi,
    cron_hour: cronHour,
    cron_minute: cronMinute,
  })
}

/**
 * 停止定时调度器
 */
export function stopScheduler() {
  return api.post<{ message: string }>('/scheduler/stop')
}

/**
 * 查询调度器当前状态
 */
export function fetchSchedulerStatus() {
  return api.get<SchedulerStatusResponse>('/scheduler/status')
}
