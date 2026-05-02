<template>
  <div class="min-h-screen bg-gray-50">
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4 flex items-center justify-between">
        <div class="flex items-center gap-4">
          <router-link to="/" class="text-gray-500 hover:text-gray-700">&larr; 返回</router-link>
          <h1 class="text-lg font-bold">{{ date }} {{ strategyLabel }}选股报告</h1>
        </div>
        <router-link to="/execute" class="text-sm text-blue-600 hover:underline">任务执行</router-link>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6 space-y-4">
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>
      <template v-else>
        <MarketOverview v-if="data" :market="data.market" />

        <div class="flex flex-wrap gap-3 rounded-lg bg-white p-3 shadow">
          <select
            v-model="sector"
            @change="loadDetail"
            class="rounded border px-3 py-1.5 text-sm"
          >
            <option value="">全部行业</option>
            <option v-for="s in data?.available_sectors" :key="s" :value="s">{{ s }}</option>
          </select>
          <input
            v-model="keyword"
            @keyup.enter="loadDetail"
            placeholder="搜索名称/代码"
            class="rounded border px-3 py-1.5 text-sm flex-1 min-w-[150px]"
          />
          <button @click="loadDetail" class="rounded bg-blue-600 px-4 py-1.5 text-sm text-white">
            搜索
          </button>
        </div>

        <div v-if="data && data.stocks.length > 0" class="rounded-lg bg-white shadow">
          <ScoreTable
            :stocks="data.stocks"
            :strategy="strategy"
            :current-sort="sortBy"
            :current-order="order"
            @sort="onSort"
          />
        </div>
        <div v-else class="py-12 text-center text-gray-500">当日无入选股票</div>
      </template>
    </main>
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

async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchReportDetail(
      date, strategy, sortBy.value, order.value,
      sector.value || undefined,
      keyword.value || undefined,
    )
    data.value = resp
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

function onSort(column: string, newOrder: string) {
  sortBy.value = column
  order.value = newOrder
  loadDetail()
}

onMounted(loadDetail)
</script>
