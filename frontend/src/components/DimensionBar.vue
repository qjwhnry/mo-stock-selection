<template>
  <div class="space-y-2">
    <div v-for="dim in allDims" :key="dim">
      <div class="flex items-center justify-between text-xs text-gray-600 mb-1">
        <span>{{ dimLabel(dim) }}</span>
        <span class="font-medium">{{ scores[dim] ?? 0 }}</span>
      </div>
      <van-progress :percentage="scores[dim] ?? 0" :show-pivot="false" stroke-width="6" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { dimLabel } from '../api'

const SHORT_DIMS = ['limit', 'moneyflow', 'lhb', 'sector', 'theme']
const SWING_DIMS = ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst', 'risk_liquidity']

const props = defineProps<{
  scores: Record<string, number>
  strategy?: string
}>()

const allDims = computed(() => {
  return props.strategy === 'swing' ? SWING_DIMS : SHORT_DIMS
})
</script>
