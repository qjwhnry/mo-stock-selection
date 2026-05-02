import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// 维度中英文映射
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

export function dimLabel(key: string): string {
  const zh = DIM_LABELS[key]
  return zh ? `${zh} (${key})` : key
}

// Type definitions
export interface ReportListItem {
  trade_date: string
  strategy: string
  count: number
  avg_score: number
  max_score: number
}

export interface ReportListResponse {
  items: ReportListItem[]
  total: number
  page: number
  page_size: number
}

export interface IndexSnapshot {
  close: number
  pct_chg: number
}

export interface MarketData {
  sh_index: IndexSnapshot
  hs300_index: IndexSnapshot
  regime_score: number
}

export interface StockItem {
  rank: number
  ts_code: string
  name: string
  industry: string
  final_score: number
  rule_score: number
  ai_score: number | null
  scores: Record<string, number>
  ai_summary: string | null
  picked: boolean
}

export interface ReportDetailResponse {
  trade_date: string
  strategy: string
  market: MarketData
  stocks: StockItem[]
  available_sectors: string[]
}

export interface AiAnalysisData {
  thesis: string
  key_catalysts: string[] | null
  risks: string[] | null
  suggested_entry: string | null
  stop_loss: string | null
}

export interface RecentPick {
  trade_date: string
  picked: boolean
  final_score: number
}

export interface StockDetailResponse {
  ts_code: string
  name: string
  industry: string
  latest_scores: Record<string, number>
  ai_analysis: AiAnalysisData | null
  recent_picks: RecentPick[]
}

// API methods
export function fetchReports(strategy: string, page: number, pageSize: number) {
  return api.get<ReportListResponse>('/reports', {
    params: { strategy, page, page_size: pageSize },
  })
}

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

export function fetchStockDetail(tsCode: string, strategy: string, days = 10) {
  return api.get<StockDetailResponse>(`/stocks/${tsCode}`, {
    params: { strategy, days },
  })
}

// Task execution types
export interface TaskStatusResponse {
  task_id: string | null
  status: 'running' | 'idle' | 'error'
  strategy: string | null
  trade_date: string | null
  started_at: string | null
  error: string | null
}

export interface SchedulerStatusResponse {
  status: 'running' | 'stopped'
  strategy: string | null
  cron: string | null
  next_run: string | null
}

// Task execution methods
export function runTask(strategy: string, tradeDate?: string, skipAi = false) {
  return api.post<TaskStatusResponse>('/tasks/run', {
    strategy,
    trade_date: tradeDate || null,
    skip_ai: skipAi,
  })
}

export function fetchTaskStatus() {
  return api.get<TaskStatusResponse>('/tasks/status')
}

export function startScheduler(strategy: string, skipAi = false, cronHour = 15, cronMinute = 30) {
  return api.post<{ message: string; cron: string }>('/scheduler/start', {
    strategy,
    skip_ai: skipAi,
    cron_hour: cronHour,
    cron_minute: cronMinute,
  })
}

export function stopScheduler() {
  return api.post<{ message: string }>('/scheduler/stop')
}

export function fetchSchedulerStatus() {
  return api.get<SchedulerStatusResponse>('/scheduler/status')
}
