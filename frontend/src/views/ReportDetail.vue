<script setup lang="ts">
/**
 * 报告详情页
 *
 * 功能：
 * 1. 展示指定日期的选股报告（某一天入选的所有股票）
 * 2. 显示市场概况（上证、沪深300、Regime评分）
 * 3. 支持按行业筛选、按名称/代码搜索
 * 4. 支持按各维度排序
 * 5. 点击股票可展开查看各维度评分和AI摘要
 * 6. 点击股票可跳转个股详情页
 */

import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchReportDetail, fetchDimensionTop, dimLabel, type ReportDetailResponse, type DimensionTopResponse } from '../api'
import MarketOverview from '../components/MarketOverview.vue'
import ScoreTable from '../components/ScoreTable.vue'

const route = useRoute()

// 从 URL 路由参数获取日期和策略
const date = route.params.date as string
const strategy = (route.query.strategy as string) || 'short'

// 策略中文标签（用于导航栏标题）
const strategyLabel = computed(() => strategy === 'swing' ? '波段' : '短线')

// 报告详情数据
const data = ref<ReportDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')

// 排序相关状态
const sortBy = ref('final_score')
const order = ref('desc')

// 筛选相关状态
const sector = ref('')            // 选中的行业过滤器（空=全部）
const keyword = ref('')           // 名称/代码搜索关键字
const showSectorPicker = ref(false) // 行业选择器弹窗

/**
 * 行业选择器选项：
 * 第一项为"全部行业"，后续为后端返回的 available_sectors 列表
 */
const sectorOptions = computed(() => {
  const sectors = data.value?.available_sectors || []
  return [{ text: '全部行业', value: '' }, ...sectors.map(s => ({ text: s, value: s }))]
})

// 当前选中的行业中文标签（用于展示）
const sectorLabel = computed(() => {
  if (!sector.value) return '全部行业'
  return sector.value
})

/**
 * 行业选择器确认回调：更新 sector 并重新加载数据
 */
function onSectorConfirm({ selectedValues }: { selectedValues: string[] }) {
  sector.value = selectedValues[0] || ''
  showSectorPicker.value = false
  loadDetail()
}

/**
 * 从后端加载报告详情数据
 * @param scrollAfter 加载完成后是否滚动到股票列表区域
 */
async function loadDetail(scrollAfter = false) {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchReportDetail(
      date, strategy, sortBy.value, order.value,
      sector.value || undefined,
      keyword.value || undefined,
    )
    data.value = resp
    // 加载完成后平滑滚动到股票列表
    if (scrollAfter) {
      const el = document.querySelector('[data-stocks]')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

/**
 * 排序变化回调：更新排序参数并重新加载数据
 */
/**
 * 统计当日入选股票的行业分布（按数量降序）
 */
const sectorDistribution = computed(() => {
  if (!data.value?.stocks) return []
  const counts: Record<string, number> = {}
  for (const s of data.value.stocks) {
    const ind = s.industry || '未知'
    counts[ind] = (counts[ind] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})

function onSort(column: string, newOrder: string) {
  sortBy.value = column
  order.value = newOrder
  loadDetail(true)
}

// ================= 维度专项榜 =================
const showDimTop = ref(false)               // 是否展开维度专项榜
const dimTopLoading = ref(false)            // 维度专项榜加载状态
const dimTopData = ref<DimensionTopResponse | null>(null)  // 维度专项榜数据
const dimTopError = ref('')                // 维度专项榜错误信息
const dimTopDim = ref('')                   // 当前选中的维度
const showDimPicker = ref(false)            // 维度选择器弹窗

/** 维度专项榜可选维度（与策略相关，不含 exhaustion / risk_liquidity 等纯风险维度） */
const dimTopOptions = computed(() => {
  if (strategy === 'swing') {
    const dims = ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst']
    return dims.map(d => ({ text: dimLabel(d), value: d }))
  }
  // short 策略：排除 exhaustion（纯风险维度，不能独立选股）
  const dims = ['limit', 'limit_restart', 'moneyflow', 'lhb', 'sector', 'theme']
  return dims.map(d => ({ text: dimLabel(d), value: d }))
})

/** 维度选择器确认回调 */
function onDimPickerConfirm({ selectedValues }: { selectedValues: string[] }) {
  const selected = selectedValues[0]
  if (selected) {
    dimTopDim.value = selected
    dimTopData.value = null   // 切换维度时清空旧榜单，避免旧数据残留
    dimTopError.value = ''
    loadDimTop()
  }
  showDimPicker.value = false
}

/** 切换维度专项榜展开/收起 */
function toggleDimTop() {
  showDimTop.value = !showDimTop.value
  if (showDimTop.value && dimTopDim.value && !dimTopData.value && !dimTopLoading.value) {
    loadDimTop()
  }
}

/** 加载维度专项榜数据 */
async function loadDimTop() {
  if (!dimTopDim.value) return
  const requestDim = dimTopDim.value
  dimTopLoading.value = true
  dimTopError.value = ''
  try {
    const { data: resp } = await fetchDimensionTop(date, requestDim, strategy)
    if (dimTopDim.value === requestDim) {
      dimTopData.value = resp
    }
  } catch (e: any) {
    if (dimTopDim.value === requestDim) {
      dimTopData.value = null
      dimTopError.value = e?.response?.data?.detail || '加载失败'
    }
  } finally {
    if (dimTopDim.value === requestDim) {
      dimTopLoading.value = false
    }
  }
}

// 组件挂载时加载数据
onMounted(() => loadDetail())
</script>

<template>
  <div class="min-h-screen bg-gray-50">

    <!-- 顶部导航栏 -->
    <van-nav-bar
      :title="`${date} ${strategyLabel}选股报告`"
      left-text="返回"
      left-arrow
      @click-left="$router.push('/')"
    >
      <template #right>
        <!-- 跳转执行页面 -->
        <router-link to="/execute" class="text-sm text-blue-600">执行</router-link>
      </template>
    </van-nav-bar>

    <div class="px-3 py-4 space-y-3">

      <!-- 加载中 -->
      <div v-if="loading" class="py-12 text-center">
        <van-loading size="24px">加载中...</van-loading>
      </div>

      <!-- 错误提示 -->
      <van-empty v-else-if="error" :description="error" />

      <template v-else>

        <!-- 市场概况卡片（上证、沪深300、Regime评分） -->
        <MarketOverview v-if="data" :market="data.market" />

        <!-- 筛选栏：搜索框 + 行业选择 -->
        <van-cell-group inset>
          <!-- 名称/代码搜索 -->
          <van-field
            v-model="keyword"
            placeholder="搜索名称/代码"
            clearable
          >
            <template #left-icon>
              <span class="text-gray-400">🔍</span>
            </template>
            <template #button>
              <van-button size="small" type="primary" @click="loadDetail()">搜索</van-button>
            </template>
          </van-field>

          <!-- 行业选择（只读，点击弹出选择器） -->
          <van-field
            :model-value="sectorLabel"
            is-link
            readonly
            placeholder="全部行业"
            @click="showSectorPicker = true"
          />
        </van-cell-group>

        <!-- 行业选择弹窗 -->
        <van-popup v-model:show="showSectorPicker" position="bottom" round>
          <van-picker
            :columns="sectorOptions"
            @confirm="onSectorConfirm"
            @cancel="showSectorPicker = false"
          />
        </van-popup>

        <!-- 行业分布概览 -->
        <div v-if="sectorDistribution.length > 0" class="sector-distro">
          <span class="text-xs text-gray-400 mr-2">行业分布</span>
          <span
            v-for="[name, cnt] in sectorDistribution"
            :key="name"
            class="sector-tag"
          >{{ name }} <strong>{{ cnt }}</strong></span>
        </div>

        <!-- 股票列表（可折叠展开） -->
        <div data-stocks>
          <template v-if="data && data.stocks.length > 0">
            <ScoreTable
              :stocks="data.stocks"
              :strategy="strategy"
              :trade-date="date"
              :current-sort="sortBy"
              :current-order="order"
              @sort="onSort"
            />
          </template>
          <van-empty v-else description="当日无入选股票" />
        </div>

        <!-- 维度专项榜：按单一维度从全候选池排名，独立于综合选股结果 -->
        <div class="dim-top-section mt-6">
          <div class="flex items-center justify-between" :class="{ 'mb-3': showDimTop }">
            <div
              class="flex items-center gap-2 text-sm font-medium text-gray-700 cursor-pointer"
              @click="toggleDimTop"
            >
              <span>{{ showDimTop ? '▼' : '▶' }} 维度专项榜</span>
              <span class="text-xs text-gray-400 font-normal">
                （从全候选池按单维度排名，独立于综合入选名单）
              </span>
            </div>
            <span v-if="dimTopDim" class="text-xs text-blue-500 bg-blue-50 px-2 py-1 rounded">
              当前：{{ dimLabel(dimTopDim) }}
            </span>
          </div>

          <template v-if="showDimTop">
            <!-- 维度选择器 -->
            <van-cell-group inset class="mb-3">
              <van-field
                :model-value="dimTopDim ? dimLabel(dimTopDim) : '请选择维度'"
                is-link
                readonly
                label="排名依据"
                placeholder="请选择维度"
                @click="showDimPicker = true"
              />
            </van-cell-group>

            <!-- 维度选择弹窗 -->
            <van-popup v-model:show="showDimPicker" position="bottom" round>
              <van-picker
                :columns="dimTopOptions"
                @confirm="onDimPickerConfirm"
                @cancel="showDimPicker = false"
              />
            </van-popup>

            <!-- 加载中 -->
            <div v-if="dimTopLoading" class="py-6 text-center">
              <van-loading size="20px">加载中...</van-loading>
            </div>

            <!-- 请求失败 -->
            <van-empty v-else-if="dimTopError" :description="dimTopError" />

            <!-- 结果列表 -->
            <template v-else-if="dimTopData && dimTopData.stocks.length > 0">
              <div class="text-xs text-gray-400 px-2 mb-2">
                共 {{ dimTopData.stocks.length }} 只，
                <span class="text-green-600 font-medium">{{ dimTopData.stocks.filter(s => s.picked).length }} 只</span>
                已入选今日综合名单
              </div>
              <ScoreTable
                :stocks="dimTopData.stocks"
                :strategy="strategy"
                :trade-date="date"
                :current-sort="dimTopDim"
                :current-order="'desc'"
                :show-sort="false"
              />
            </template>

            <!-- 零结果：该维度无候选 -->
            <van-empty v-else-if="dimTopDim && dimTopData" description="该维度暂无候选" />

            <!-- 未选择维度时的提示 -->
            <div v-else-if="!dimTopDim" class="py-6 text-center text-sm text-gray-400">
              请先选择排名依据维度
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.sector-distro {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid rgba(31, 42, 37, 0.06);
  box-shadow: 0 12px 30px rgba(31, 42, 37, 0.04);
}

.sector-tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 12px;
  color: #374151;
  background: #f3f4f6;
  border-radius: 4px;
  white-space: nowrap;
}

.sector-tag strong {
  font-weight: 600;
  color: #1f2a25;
}
</style>
