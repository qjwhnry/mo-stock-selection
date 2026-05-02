<template>
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

function sortIndicator(column: string) {
  if (props.currentSort !== column) return ''
  return props.currentOrder === 'desc' ? '↓' : '↑'
}
</script>
