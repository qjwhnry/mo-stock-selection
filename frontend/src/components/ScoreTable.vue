<template>
  <div class="space-y-3">
    <!-- Sort controls -->
    <van-cell-group inset>
      <van-field
        :model-value="sortLabel"
        is-link
        readonly
        label="排序"
        @click="showSortPicker = true"
      />
      <van-field label="方向">
        <template #input>
          <van-button size="small" @click="toggleOrder">
            {{ currentOrder === 'desc' ? '↓ 降序' : '↑ 升序' }}
          </van-button>
        </template>
      </van-field>
    </van-cell-group>

    <van-popup v-model:show="showSortPicker" position="bottom" round>
      <van-picker :columns="sortOptions" @confirm="onSortConfirm" @cancel="showSortPicker = false" />
    </van-popup>

    <!-- Stock list with collapse -->
    <p class="text-xs text-gray-400 px-2">点击股票展开查看维度评分</p>
    <van-collapse v-model="expandedNames">
      <van-collapse-item
        v-for="stock in stocks"
        :key="stock.ts_code"
        :title="stock.name"
        :label="`${stock.ts_code} · ${stock.industry}`"
        :name="stock.ts_code"
      >
        <template #value>
          <span class="text-blue-600 font-medium">{{ stock.final_score }}</span>
          <span class="text-gray-400 text-xs ml-1">排名{{ stock.rank }}</span>
        </template>

        <DimensionBar :scores="stock.scores" :strategy="strategy" />
        <AiSummary :summary="stock.ai_summary" />

        <div class="mt-2">
          <router-link
            :to="`/stock/${stock.ts_code}?strategy=${strategy}`"
            class="text-blue-600 text-sm"
          >
            查看详情 →
          </router-link>
        </div>
      </van-collapse-item>
    </van-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { StockItem } from '../api'
import { dimLabel } from '../api'
import DimensionBar from './DimensionBar.vue'
import AiSummary from './AiSummary.vue'

const props = defineProps<{
  stocks: StockItem[]
  strategy: string
  currentSort: string
  currentOrder: string
}>()

const emit = defineEmits<{
  sort: [sortBy: string, order: string]
}>()

const showSortPicker = ref(false)
const expandedNames = ref<string[]>([])

const sortOptions = computed(() => {
  const base = [
    { text: '综合分', value: 'final_score' },
    { text: '规则分', value: 'rule_score' },
    { text: 'AI 分', value: 'ai_score' },
  ]
  const dims = props.strategy === 'swing'
    ? ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst', 'risk_liquidity']
    : ['limit', 'moneyflow', 'lhb', 'sector', 'theme']
  const dimOpts = dims.map(d => ({ text: dimLabel(d), value: d }))
  return [...base, ...dimOpts]
})

const sortLabel = computed(() => {
  const opt = sortOptions.value.find(o => o.value === props.currentSort)
  return opt?.text || props.currentSort
})

function onSortConfirm({ selectedValues }: { selectedValues: string[] }) {
  showSortPicker.value = false
  emit('sort', selectedValues[0], props.currentOrder)
}

function toggleOrder() {
  const newOrder = props.currentOrder === 'desc' ? 'asc' : 'desc'
  emit('sort', props.currentSort, newOrder)
}
</script>
