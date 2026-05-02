<template>
  <div class="min-h-screen bg-gray-50">
    <van-nav-bar
      :title="`${date} ${strategyLabel}选股报告`"
      left-text="返回"
      left-arrow
      @click-left="$router.push('/')"
    >
      <template #right>
        <router-link to="/execute" class="text-sm text-blue-600">执行</router-link>
      </template>
    </van-nav-bar>

    <div class="px-3 py-4 space-y-3">
      <div v-if="loading" class="py-12 text-center">
        <van-loading size="24px">加载中...</van-loading>
      </div>
      <van-empty v-else-if="error" :description="error" />
      <template v-else>
        <MarketOverview v-if="data" :market="data.market" />

        <!-- 筛选栏 -->
        <van-cell-group inset>
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
          <van-field
            :model-value="sectorLabel"
            is-link
            readonly
            placeholder="全部行业"
            @click="showSectorPicker = true"
          />
        </van-cell-group>

        <van-popup v-model:show="showSectorPicker" position="bottom" round>
          <van-picker
            :columns="sectorOptions"
            @confirm="onSectorConfirm"
            @cancel="showSectorPicker = false"
          />
        </van-popup>

        <!-- 股票列表 -->
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

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchReportDetail, type ReportDetailResponse } from '../api'
import MarketOverview from '../components/MarketOverview.vue'
import ScoreTable from '../components/ScoreTable.vue'

const route = useRoute()
const date = route.params.date as string
const strategy = (route.query.strategy as string) || 'short'

const strategyLabel = computed(() => strategy === 'swing' ? '波段' : '短线')

const data = ref<ReportDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')

const sortBy = ref('final_score')
const order = ref('desc')
const sector = ref('')
const keyword = ref('')
const showSectorPicker = ref(false)

const sectorOptions = computed(() => {
  const sectors = data.value?.available_sectors || []
  return [{ text: '全部行业', value: '' }, ...sectors.map(s => ({ text: s, value: s }))]
})

const sectorLabel = computed(() => {
  if (!sector.value) return '全部行业'
  return sector.value
})

function onSectorConfirm({ selectedValues }: { selectedValues: string[] }) {
  sector.value = selectedValues[0] || ''
  showSectorPicker.value = false
  loadDetail()
}

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

function onSort(column: string, newOrder: string) {
  sortBy.value = column
  order.value = newOrder
  loadDetail(true)
}

onMounted(() => loadDetail())
</script>
