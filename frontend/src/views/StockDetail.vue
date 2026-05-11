<script setup lang="ts">
/**
 * 个股详情页
 *
 * 功能：
 * 1. 展示单只股票的详细信息（名称、行业）
 * 2. 各维度评分柱状图（DimensionBar）
 * 3. AI 深度分析结果（论点、催化剂、风险、建议入场、止损）
 * 4. 该股近期 N 天的选股记录（是否入选 + 综合分）
 */

import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchStockDetail, fetchStockSignals, type StockDetailResponse, type StockSignalsResponse } from '../api'
import DimensionBar from '../components/DimensionBar.vue'

const route = useRoute()

// 从 URL 获取股票代码、策略和可选的日期参数
const code = route.params.code as string
const strategy = (route.query.strategy as string) || 'short'
const tradeDate = (route.query.trade_date as string) || ''

// 股票详情数据
const data = ref<StockDetailResponse | null>(null)
const signals = ref<StockSignalsResponse | null>(null)
const loading = ref(true)
const signalsLoading = ref(false)
const error = ref('')
const activeDetail = ref('')

function todayText(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

function fmt(value: number | null | undefined, suffix = '') {
  if (value === null || value === undefined) return '暂无'
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${suffix}`
}

/**
 * 加载个股详情数据
 */
async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchStockDetail(code, strategy, 10, tradeDate || undefined)
    data.value = resp
    await loadSignals(resp)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

async function loadSignals(detail: StockDetailResponse) {
  signalsLoading.value = true
  try {
    const endDate = tradeDate || detail.recent_picks[0]?.trade_date || todayText()
    const { data: resp } = await fetchStockSignals(code, endDate, strategy, 20)
    signals.value = resp
  } catch {
    signals.value = null
  } finally {
    signalsLoading.value = false
  }
}

/**
 * 从 signals.scores 中提取前一交易日的各维度得分，用于计算趋势
 */
const prevScores = computed(() => {
  if (!signals.value?.scores?.length) return null
  // scores 按 trade_date ASC 排序，取倒数第二个日期
  const byDate: Record<string, Record<string, number>> = {}
  for (const s of signals.value.scores) {
    if (!byDate[s.trade_date]) byDate[s.trade_date] = {}
    byDate[s.trade_date][s.dim] = s.score
  }
  const dates = Object.keys(byDate).sort()
  if (dates.length < 2) return null
  return byDate[dates[dates.length - 2]]
})

/**
 * 当前维度得分与前一交易日的差值
 */
const scoreDiffs = computed(() => {
  if (!data.value || !prevScores.value) return {}
  const diffs: Record<string, number> = {}
  for (const [dim, score] of Object.entries(data.value.latest_scores)) {
    const prev = prevScores.value[dim]
    if (prev !== undefined) diffs[dim] = Math.round((score - prev) * 10) / 10
  }
  return diffs
})

/**
 * 资金流数据中最大绝对 net_mf_ratio_pct，用于归一化迷你柱宽度
 */
const mfMaxRatio = computed(() => {
  if (!signals.value?.moneyflow?.length) return 0
  return Math.max(...signals.value.moneyflow.map(m => Math.abs(m.net_mf_ratio_pct ?? 0)))
})

onMounted(loadDetail)
</script>

<template>
  <div class="min-h-screen bg-gray-50">

    <!-- 顶部导航栏：显示股票名称 -->
    <van-nav-bar
      :title="`${code} ${data?.name || ''}`"
      left-text="返回"
      left-arrow
      @click-left="$router.back()"
    />

    <div class="px-3 py-4 space-y-3">

      <!-- 加载中 -->
      <div v-if="loading" class="py-12 text-center">
        <van-loading size="24px">加载中...</van-loading>
      </div>

      <!-- 错误提示 -->
      <van-empty v-else-if="error" :description="error" />

      <template v-else-if="data">

        <!-- 基本信息卡片 -->
        <van-cell-group inset>
          <van-cell title="行业" :value="data.industry" />
          <van-cell v-if="data.concepts.length" title="概念题材">
            <template #value>
              <div class="flex flex-wrap gap-1 justify-end">
                <van-tag
                  v-for="c in data.concepts"
                  :key="c"
                  type="primary"
                  size="medium"
                  plain
                >{{ c }}</van-tag>
                <span v-if="data.concept_count > data.concepts.length" class="text-xs text-gray-400">+{{ data.concept_count - data.concepts.length }}</span>
              </div>
            </template>
          </van-cell>
          <van-cell title="AI 评分" :value="data.ai_score != null ? String(data.ai_score) : '暂无'" />
        </van-cell-group>

        <!-- 各维度评分柱状图（含打分详情） -->
        <van-cell-group inset title="维度打分">
          <van-cell>
            <DimensionBar :scores="data.latest_scores" :score-details="data.score_details" :strategy="strategy" :score-diffs="scoreDiffs" />
          </van-cell>
        </van-cell-group>

        <!-- AI 深度分析结果 -->
        <van-cell-group inset title="AI 深度分析">
          <template v-if="data.ai_analysis">
            <van-cell title="核心论点">
              <template #label>
                <p class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{{ data.ai_analysis.thesis }}</p>
              </template>
            </van-cell>
            <van-cell v-if="data.ai_analysis.key_catalysts?.length" title="关键催化剂">
              <template #label>
                <div class="flex flex-wrap gap-1.5 mt-1">
                  <span v-for="c in data.ai_analysis.key_catalysts" :key="c" class="inline-block px-2 py-0.5 text-xs rounded-full bg-green-50 text-green-700 border border-green-200">{{ c }}</span>
                </div>
              </template>
            </van-cell>
            <van-cell v-if="data.ai_analysis.risks?.length" title="风险提示">
              <template #label>
                <div class="flex flex-wrap gap-1.5 mt-1">
                  <span v-for="r in data.ai_analysis.risks" :key="r" class="inline-block px-2 py-0.5 text-xs rounded-full bg-red-50 text-red-700 border border-red-200">{{ r }}</span>
                </div>
              </template>
            </van-cell>
            <van-cell
              v-if="data.ai_analysis.suggested_entry"
              title="建议入场"
              :value="data.ai_analysis.suggested_entry"
            />
            <van-cell
              v-if="data.ai_analysis.stop_loss"
              title="止损"
              :value="data.ai_analysis.stop_loss"
              value-class="text-red-600"
            />
          </template>
          <van-cell v-else title="AI 分析缺失" />
        </van-cell-group>

        <!-- 近期选股记录 -->
        <van-cell-group inset title="近期选股记录">
          <van-cell>
            <div class="recent-picks-table">
              <div class="pick-header">
                <span class="pick-col-date">日期</span>
                <span class="pick-col-status">状态</span>
                <span class="pick-col-score">评分</span>
                <span class="pick-col-fwd">5日收益</span>
                <span class="pick-col-fwd">10日收益</span>
              </div>
              <div v-for="p in data.recent_picks" :key="p.trade_date" class="pick-row" :class="{ 'pick-row--selected': p.picked }">
                <span class="pick-col-date">{{ p.trade_date }}</span>
                <span class="pick-col-status">
                  <span v-if="p.picked" class="text-blue-600 font-medium">入选</span>
                  <span v-else class="text-gray-400">未入选</span>
                </span>
                <span class="pick-col-score">{{ p.final_score }}</span>
                <span v-if="p.forward_return_5d != null" class="pick-col-fwd" :class="p.forward_return_5d >= 0 ? 'text-red-500' : 'text-green-500'">
                  {{ p.forward_return_5d >= 0 ? '+' : '' }}{{ p.forward_return_5d }}%
                </span>
                <span v-else class="pick-col-fwd text-gray-300">-</span>
                <span v-if="p.forward_return_10d != null" class="pick-col-fwd" :class="p.forward_return_10d >= 0 ? 'text-red-500' : 'text-green-500'">
                  {{ p.forward_return_10d >= 0 ? '+' : '' }}{{ p.forward_return_10d }}%
                </span>
                <span v-else class="pick-col-fwd text-gray-300">-</span>
              </div>
            </div>
          </van-cell>
        </van-cell-group>

        <!-- 原始数据明细：折叠展示，避免页面过长 -->
        <van-cell-group inset title="数据明细">
          <van-cell v-if="signalsLoading">
            <van-loading size="20px">加载中...</van-loading>
          </van-cell>
          <van-collapse v-else-if="signals" v-model="activeDetail" accordion>
            <van-collapse-item title="近 20 日资金流" name="moneyflow">
              <van-empty v-if="signals.moneyflow.length === 0" description="暂无资金流数据" />
              <div v-else class="signal-list">
                <div v-for="row in signals.moneyflow" :key="row.trade_date" class="signal-row">
                  <div class="flex items-center justify-between">
                    <strong>{{ row.trade_date }}</strong>
                    <span :class="(row.net_mf_ratio_pct ?? 0) >= 0 ? 'text-red-500' : 'text-green-500'">
                      {{ fmt(row.net_mf_wan, ' 万') }}
                    </span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-xs text-gray-400 w-16">占成交额 {{ fmt(row.net_mf_ratio_pct, '%') }}</span>
                    <div class="mf-bar-track">
                      <div
                        class="mf-bar-fill"
                        :class="(row.net_mf_ratio_pct ?? 0) >= 0 ? 'bg-red-400' : 'bg-green-400'"
                        :style="{
                          width: mfMaxRatio > 0 ? (Math.abs(row.net_mf_ratio_pct ?? 0) / mfMaxRatio * 100).toFixed(0) + '%' : '0%',
                        }"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </van-collapse-item>
            <van-collapse-item title="龙虎榜记录" name="lhb">
              <van-empty v-if="signals.lhb.length === 0" description="暂无龙虎榜数据" />
              <div v-else class="signal-list">
                <div v-for="row in signals.lhb" :key="row.trade_date" class="signal-row">
                  <strong>{{ row.trade_date }}</strong>
                  <span>净买 {{ fmt(row.lhb_net_amount_wan, ' 万') }}</span>
                  <span>净买占比 {{ fmt(row.lhb_net_rate_pct, '%') }}</span>
                  <span>{{ row.reason || '暂无上榜原因' }}</span>
                </div>
              </div>
            </van-collapse-item>
          </van-collapse>
          <van-cell v-else title="数据明细缺失" />
        </van-cell-group>

      </template>
    </div>
  </div>
</template>

<style scoped>
.signal-list {
  display: grid;
  gap: 4px;
}

.signal-row {
  padding: 6px 0;
  border-bottom: 1px solid #eef0f2;
  color: #4b5563;
  font-size: 13px;
}

.signal-row:last-child {
  border-bottom: 0;
}

.signal-row strong {
  color: #111827;
}

/* 近期选股记录表格 */
.recent-picks-table {
  font-size: 13px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.pick-header {
  display: flex;
  gap: 8px;
  padding: 4px 0 6px;
  color: #9ca3af;
  font-size: 12px;
  border-bottom: 1px solid #eef0f2;
}

.pick-row {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid #f3f4f6;
}

.pick-row:last-child {
  border-bottom: 0;
}

.pick-row--selected {
  background: #f8faff;
}

.pick-col-date {
  width: 90px;
  flex-shrink: 0;
  color: #374151;
}

.pick-col-status {
  width: 44px;
  flex-shrink: 0;
  font-size: 12px;
}

.pick-col-score {
  width: 36px;
  flex-shrink: 0;
  text-align: right;
  color: #374151;
}

.pick-col-fwd {
  width: 60px;
  flex-shrink: 0;
  text-align: right;
  font-weight: 500;
  font-size: 12px;
}

/* 资金流迷你柱 */
.mf-bar-track {
  flex: 1;
  height: 6px;
  background: #f3f4f6;
  border-radius: 3px;
  overflow: hidden;
}

.mf-bar-fill {
  height: 100%;
  border-radius: 3px;
  min-width: 2px;
  transition: width 0.3s ease;
}
</style>
