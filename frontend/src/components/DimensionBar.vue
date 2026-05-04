<script setup lang="ts">
/**
 * 维度评分柱状图组件
 *
 * 功能：展示一只股票各维度的得分进度条
 * - short 策略展示 5 个维度：limit、moneyflow、lhb、sector、theme
 * - swing 策略展示 7 个维度：trend、pullback、moneyflow_swing、sector_swing、theme_swing、catalyst、risk_liquidity
 */

import { computed } from 'vue'
import { dimLabel } from '../api'

// short 策略的 5 个维度
const SHORT_DIMS = ['limit', 'moneyflow', 'lhb', 'sector', 'theme']
// swing 策略的 7 个维度
const SWING_DIMS = ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst', 'risk_liquidity']

const props = defineProps<{
  scores: Record<string, number>   // 维度名称 -> 得分的映射
  strategy?: string                 // 当前策略类型（空则默认 short）
}>()

/**
 * 根据策略类型返回对应的维度列表
 */
const allDims = computed(() => {
  return props.strategy === 'swing' ? SWING_DIMS : SHORT_DIMS
})
</script>

<template>
  <!-- 遍历各维度，渲染标签 + 进度条 -->
  <div class="space-y-2">
    <div v-for="dim in allDims" :key="dim">
      <!-- 维度名称 + 得分 -->
      <div class="flex items-center justify-between text-xs text-gray-600 mb-1">
        <span>{{ dimLabel(dim) }}</span>
        <span class="font-medium">{{ scores[dim] ?? 0 }}</span>
      </div>
      <!-- Vant 进度条：百分比即得分（得分范围 0-100） -->
      <van-progress :percentage="scores[dim] ?? 0" :show-pivot="false" stroke-width="6" />
    </div>
  </div>
</template>