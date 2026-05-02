import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

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
