<template>
  <div class="min-h-screen bg-gray-50">
    <van-nav-bar
      :title="`${code} ${data?.name || ''}`"
      left-text="返回"
      left-arrow
      @click-left="$router.back()"
    />

    <div class="px-3 py-4 space-y-3">
      <div v-if="loading" class="py-12 text-center">
        <van-loading size="24px">加载中...</van-loading>
      </div>
      <van-empty v-else-if="error" :description="error" />
      <template v-else-if="data">
        <!-- Info -->
        <van-cell-group inset>
          <van-cell title="行业" :value="data.industry" />
        </van-cell-group>

        <!-- Dimension Scores -->
        <van-cell-group inset title="维度打分">
          <van-cell>
            <DimensionBar :scores="data.latest_scores" :strategy="strategy" />
          </van-cell>
        </van-cell-group>

        <!-- AI Analysis -->
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

        <!-- Recent Picks -->
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
      </template>
    </div>
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
