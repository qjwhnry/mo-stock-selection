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
import { fetchReportDetail, type ReportDetailResponse } from '../api'
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
function onSort(column: string, newOrder: string) {
  sortBy.value = column
  order.value = newOrder
  loadDetail(true)
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

        <!-- 股票列表（可折叠展开） -->
        <div data-stocks>
          <template v-if="data && data.stocks.length > 0">
            <ScoreTable
              :stocks="data.stocks"
              :strategy="strategy"
              :current-sort="sortBy"
              :current-order="order"
              @sort="onSort"
            />
          </template>
          <van-empty v-else description="当日无入选股票" />
        </div>
      </template>
    </div>
  </div>
</template>