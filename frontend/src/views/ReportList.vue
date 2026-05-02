<template>
  <div class="min-h-screen bg-gray-50">
    <!-- Header -->
    <van-nav-bar title="mo-stock 选股系统">
      <template #right>
        <router-link to="/execute" class="text-sm text-blue-600">执行</router-link>
      </template>
    </van-nav-bar>

    <!-- Strategy Tabs -->
    <van-tabs v-model:active="strategy" @change="onStrategyChange">
      <van-tab title="短线" name="short" />
      <van-tab title="波段" name="swing" />
    </van-tabs>

    <div class="px-3 py-4">
      <!-- Loading -->
      <div v-if="loading" class="py-12 text-center text-gray-500">
        <van-loading size="24px">加载中...</van-loading>
      </div>

      <!-- Error -->
      <van-empty v-else-if="error" :description="error" />

      <!-- Empty -->
      <van-empty v-else-if="reports.length === 0" description="暂无选股数据，请先运行选股" />

      <!-- Report Cards -->
      <van-cell-group v-else inset>
        <van-cell
          v-for="r in reports"
          :key="r.trade_date"
          :title="r.trade_date"
          :label="`入选 ${r.count} 只 · 平均 ${r.avg_score.toFixed(1)} 分 · 最高 ${r.max_score.toFixed(1)}`"
          is-link
          :to="`/report/${r.trade_date}?strategy=${strategy}`"
        />
      </van-cell-group>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="mt-4">
        <van-pagination
          v-model="page"
          :total-items="total"
          :items-per-page="pageSize"
          @change="loadReports"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { fetchReports, type ReportListItem } from '../api'

const strategy = ref('short')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const reports = ref<ReportListItem[]>([])
const loading = ref(false)
const error = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

function onStrategyChange() {
  page.value = 1
  loadReports()
}

async function loadReports() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await fetchReports(strategy.value, page.value, pageSize)
    reports.value = data.items
    total.value = data.total
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadReports)
</script>
