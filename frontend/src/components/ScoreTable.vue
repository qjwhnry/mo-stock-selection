<template>
  <div class="space-y-3">
    <!-- Sort controls -->
    <div class="flex items-center gap-4 text-sm">
      <div class="flex items-center gap-2">
        <label class="text-gray-600">排序:</label>
        <select
          :value="currentSort"
          @change="handleSortChange($event)"
          class="border border-gray-300 rounded px-2 py-1 bg-white"
        >
          <option value="final_score">综合分</option>
          <option value="rule_score">规则分</option>
          <option value="ai_score">AI 分</option>
          <option disabled>──────────</option>
          <option value="limit">涨停异动</option>
          <option value="moneyflow">资金流向</option>
          <option value="lhb">龙虎榜</option>
          <option value="sector">板块</option>
          <option value="theme">题材</option>
        </select>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-gray-600">方向:</label>
        <button
          @click="toggleOrder"
          class="border border-gray-300 rounded px-3 py-1 bg-white hover:bg-gray-50 min-w-[80px]"
        >
          {{ currentOrder === 'desc' ? '↓ 降序' : '↑ 升序' }}
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="bg-gray-50">
        <tr>
          <th class="px-3 py-2 text-left">排名</th>
          <th class="px-3 py-2 text-left">代码</th>
          <th class="px-3 py-2 text-left">名称</th>
          <th class="px-3 py-2 text-left">行业</th>
          <th class="px-3 py-2 text-center cursor-pointer" @click="toggleSort('final_score')">
            综合分 {{ sortIndicator('final_score') }}
          </th>
          <th class="px-3 py-2 text-left">操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="stock in stocks" :key="stock.ts_code">
          <tr class="border-t hover:bg-gray-50 cursor-pointer" @click="toggleExpand(stock.ts_code)">
            <td class="px-3 py-2">{{ stock.rank }}</td>
            <td class="px-3 py-2">{{ stock.ts_code }}</td>
            <td class="px-3 py-2">{{ stock.name }}</td>
            <td class="px-3 py-2">{{ stock.industry }}</td>
            <td class="px-3 py-2 text-center font-medium">{{ stock.final_score }}</td>
            <td class="px-3 py-2">
              <router-link
                :to="`/stock/${stock.ts_code}?strategy=${strategy}`"
                class="text-blue-600 hover:underline"
                @click.stop
              >
                详情
              </router-link>
            </td>
          </tr>
          <tr v-if="expanded === stock.ts_code" class="border-t bg-gray-50">
            <td colspan="6" class="px-4 py-3">
              <DimensionBar :scores="stock.scores" />
              <AiSummary :summary="stock.ai_summary" />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { StockItem } from '../api'
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

const expanded = ref<string | null>(null)

function toggleExpand(code: string) {
  expanded.value = expanded.value === code ? null : code
}

function toggleSort(column: string) {
  const newOrder = props.currentSort === column && props.currentOrder === 'desc' ? 'asc' : 'desc'
  emit('sort', column, newOrder)
}

function toggleOrder() {
  const newOrder = props.currentOrder === 'desc' ? 'asc' : 'desc'
  emit('sort', props.currentSort, newOrder)
}

function handleSortChange(event: Event) {
  const target = event.target as HTMLSelectElement
  emit('sort', target.value, props.currentOrder)
}

function sortIndicator(column: string) {
  if (props.currentSort !== column) return ''
  return props.currentOrder === 'desc' ? '↓' : '↑'
}
</script>
