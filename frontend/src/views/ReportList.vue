<template>
  <div class="home-page min-h-screen">
    <!-- Header -->
    <van-nav-bar title="mo-stock 选股系统">
      <template #right>
        <div class="flex items-center gap-3">
          <button type="button" class="text-sm text-blue-600" @click="handleLogout">退出</button>
          <router-link to="/execute" class="text-sm text-blue-600">执行</router-link>
        </div>
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
      <van-cell-group v-else inset class="report-list-card">
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
import { useRouter } from 'vue-router'
import { fetchReports, type ReportListItem } from '../api'
import { clearAuthSession } from '../auth'

const router = useRouter()
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

function handleLogout() {
  clearAuthSession()
  router.push('/login')
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

<style scoped>
.home-page {
  min-height: 100vh;
  background:
    linear-gradient(180deg, #f8faf8 0%, #f2f5f3 46%, #eef2ef 100%);
  color: #1f2a25;
}

:deep(.van-nav-bar) {
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 1px 0 rgba(31, 42, 37, 0.06);
}

:deep(.van-nav-bar__title) {
  color: #1f2a25;
  font-weight: 700;
}

:deep(.van-tabs__wrap) {
  background: rgba(255, 255, 255, 0.82);
  box-shadow: inset 0 -1px 0 rgba(31, 42, 37, 0.05);
}

:deep(.van-tabs__nav) {
  background: transparent;
}

:deep(.van-tab) {
  color: #52615b;
}

:deep(.van-tab--active) {
  color: #17201d;
  font-weight: 700;
}

:deep(.van-tabs__line) {
  background: #1f8a7f;
}

:deep(.report-list-card) {
  overflow: hidden;
  border: 1px solid rgba(31, 42, 37, 0.06);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 12px 30px rgba(31, 42, 37, 0.04);
}

:deep(.report-list-card .van-cell) {
  background: transparent;
}

:deep(.report-list-card .van-cell__title) {
  color: #24302b;
}

:deep(.report-list-card .van-cell__label) {
  color: #7a8781;
}
</style>
