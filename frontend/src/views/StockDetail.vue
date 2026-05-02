<template>
  <div class="min-h-screen bg-gray-50">
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4 flex items-center gap-4">
        <button @click="$router.back()" class="text-gray-500 hover:text-gray-700">&larr; 返回</button>
        <h1 class="text-lg font-bold">{{ code }} {{ data?.name || '' }}</h1>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6 space-y-4">
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>
      <template v-else-if="data">
        <!-- Industry -->
        <div class="rounded-lg bg-white p-4 shadow">
          <span class="text-sm text-gray-500">行业：{{ data.industry }}</span>
        </div>

        <!-- Dimension Scores -->
        <div class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">维度打分</h2>
          <DimensionBar :scores="data.latest_scores" />
        </div>

        <!-- AI Analysis -->
        <div v-if="data.ai_analysis" class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">AI 深度分析</h2>
          <div class="space-y-3 text-sm">
            <div>
              <div class="font-medium text-gray-800">核心论点</div>
              <div class="mt-1 text-gray-600">{{ data.ai_analysis.thesis }}</div>
            </div>
            <div v-if="data.ai_analysis.key_catalysts?.length">
              <div class="font-medium text-gray-800">关键催化剂</div>
              <ul class="mt-1 list-disc pl-5 text-gray-600">
                <li v-for="(c, i) in data.ai_analysis.key_catalysts" :key="i">{{ c }}</li>
              </ul>
            </div>
            <div v-if="data.ai_analysis.risks?.length">
              <div class="font-medium text-gray-800">风险提示</div>
              <ul class="mt-1 list-disc pl-5 text-gray-600">
                <li v-for="(r, i) in data.ai_analysis.risks" :key="i">{{ r }}</li>
              </ul>
            </div>
            <div v-if="data.ai_analysis.suggested_entry" class="flex gap-6">
              <div>
                <span class="font-medium text-gray-800">建议入场：</span>
                <span class="text-gray-600">{{ data.ai_analysis.suggested_entry }}</span>
              </div>
              <div v-if="data.ai_analysis.stop_loss">
                <span class="font-medium text-gray-800">止损：</span>
                <span class="text-red-600">{{ data.ai_analysis.stop_loss }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="rounded-lg bg-white p-4 shadow">
          <div class="text-sm text-gray-400">AI 分析缺失</div>
        </div>

        <!-- Recent Picks -->
        <div class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">近期选股记录</h2>
          <div class="flex flex-wrap gap-2">
            <div
              v-for="p in data.recent_picks"
              :key="p.trade_date"
              class="rounded border px-3 py-1.5 text-xs"
              :class="p.picked ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-gray-50 text-gray-400'"
            >
              {{ p.trade_date }}
              <span v-if="p.picked" class="ml-1 font-medium">{{ p.final_score }}</span>
              <span v-else class="ml-1">未入选</span>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchStockDetail, type StockDetailResponse } from '../api'
import DimensionBar from '../components/DimensionBar.vue'

const route = useRoute()
const code = route.params.code as string
const strategy = (route.query.strategy as string) || 'short'

const data = ref<StockDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')

async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchStockDetail(code, strategy)
    data.value = resp
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadDetail)
</script>
