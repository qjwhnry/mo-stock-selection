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

import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchStockDetail, fetchStockSignals, type StockDetailResponse, type StockSignalsResponse } from '../api'
import DimensionBar from '../components/DimensionBar.vue'

const route = useRoute()

// 从 URL 获取股票代码和策略参数
const code = route.params.code as string
const strategy = (route.query.strategy as string) || 'short'

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
    const { data: resp } = await fetchStockDetail(code, strategy)
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
    const endDate = detail.recent_picks[0]?.trade_date || todayText()
    const { data: resp } = await fetchStockSignals(code, endDate, strategy, 20)
    signals.value = resp
  } catch {
    signals.value = null
  } finally {
    signalsLoading.value = false
  }
}

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
        </van-cell-group>

        <!-- 各维度评分柱状图 -->
        <van-cell-group inset title="维度打分">
          <van-cell>
            <DimensionBar :scores="data.latest_scores" :strategy="strategy" />
          </van-cell>
        </van-cell-group>

        <!-- AI 深度分析结果 -->
        <van-cell-group inset title="AI 深度分析">
          <template v-if="data.ai_analysis">
            <van-cell title="核心论点" :label="data.ai_analysis.thesis" />
            <van-cell
              v-if="data.ai_analysis.key_catalysts?.length"
              title="关键催化剂"
              :label="data.ai_analysis.key_catalysts.join('；')"
            />
            <van-cell
              v-if="data.ai_analysis.risks?.length"
              title="风险提示"
              :label="data.ai_analysis.risks.join('；')"
            />
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

        <!-- 近期选股记录（标签展示） -->
        <van-cell-group inset title="近期选股记录">
          <van-cell>
            <div class="flex flex-wrap gap-2">
              <van-tag
                v-for="p in data.recent_picks"
                :key="p.trade_date"
                :type="p.picked ? 'primary' : 'default'"
                size="medium"
              >
                {{ p.trade_date }}
                <span v-if="p.picked" class="ml-1">{{ p.final_score }}</span>
                <span v-else class="ml-1">未入选</span>
              </van-tag>
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
                  <strong>{{ row.trade_date }}</strong>
                  <span>主力净流入 {{ fmt(row.net_mf_wan, ' 万') }}</span>
                  <span>占成交额 {{ fmt(row.net_mf_ratio_pct, '%') }}</span>
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
  gap: 8px;
}

.signal-row {
  display: grid;
  gap: 2px;
  padding: 8px 0;
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
</style>
