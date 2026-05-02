<template>
  <div class="min-h-screen bg-gray-50">
    <!-- Header -->
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4">
        <h1 class="text-xl font-bold text-gray-900">mo-stock 选股系统</h1>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6">
      <!-- Strategy Tabs -->
      <div class="mb-4 flex gap-2">
        <button
          v-for="s in strategies"
          :key="s.value"
          @click="strategy = s.value; page = 1; loadReports()"
          :class="[
            'rounded px-4 py-2 text-sm font-medium',
            strategy === s.value
              ? 'bg-blue-600 text-white'
              : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50',
          ]"
        >
          {{ s.label }}
        </button>
      </div>

      <!-- Loading / Error -->
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>

      <!-- Empty -->
      <div v-else-if="reports.length === 0" class="py-12 text-center text-gray-500">
        暂无选股数据，请先运行 run-once
      </div>

      <!-- Report Table -->
      <table v-else class="w-full border-collapse bg-white shadow rounded-lg overflow-hidden">
        <thead class="bg-gray-100">
          <tr>
            <th class="px-4 py-3 text-left text-sm font-medium text-gray-600">日期</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">入选数</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">平均分</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">最高分</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in reports"
            :key="r.trade_date"
            class="border-t hover:bg-gray-50"
          >
            <td class="px-4 py-3 text-sm">{{ r.trade_date }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.count }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.avg_score }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.max_score }}</td>
            <td class="px-4 py-3 text-center">
              <router-link
                :to="`/report/${r.trade_date}?strategy=${strategy}`"
                class="text-blue-600 hover:underline text-sm"
              >
                查看
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="mt-4 flex justify-center gap-2">
        <button
          @click="page--; loadReports()"
          :disabled="page <= 1"
          class="rounded border px-3 py-1 text-sm disabled:opacity-50"
        >
          上一页
        </button>
        <span class="px-3 py-1 text-sm text-gray-600">
          {{ page }} / {{ totalPages }}
        </span>
        <button
          @click="page++; loadReports()"
          :disabled="page >= totalPages"
          class="rounded border px-3 py-1 text-sm disabled:opacity-50"
        >
          下一页
        </button>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { fetchReports, type ReportListItem } from '../api'

const strategies = [
  { value: 'short', label: '短线' },
  { value: 'swing', label: '波段' },
]

const strategy = ref('short')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const reports = ref<ReportListItem[]>([])
const loading = ref(false)
const error = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

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
