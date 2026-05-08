<script setup lang="ts">
/**
 * 维度评分柱状图 + 打分详情组件
 *
 * 功能：展示一只股票各维度的得分进度条，展开可查看打分细节
 * - short 策略展示 5 个维度：limit、moneyflow、lhb、sector、theme
 * - swing 策略展示 7 个维度：trend、pullback、moneyflow_swing、sector_swing、theme_swing、catalyst、risk_liquidity
 */

import { computed } from 'vue'
import { dimLabel, detailLabel, formatDetailValue } from '../api'

// short 策略的 5 个维度
const SHORT_DIMS = ['limit', 'moneyflow', 'lhb', 'sector', 'theme']
// swing 策略的 7 个维度
const SWING_DIMS = ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst', 'risk_liquidity']

// 不展示的 detail key（内部技术字段）
const HIDDEN_KEYS = new Set(['lhb_formula_version', 'small_l2_skip_leadership'])

const props = defineProps<{
  scores: Record<string, number>
  scoreDetails?: Record<string, Record<string, any>>
  strategy?: string
}>()

const expandedDim = defineModel<string | null>('expandedDim', { default: null })

/**
 * 根据策略类型返回对应的维度列表
 */
const allDims = computed(() => {
  return props.strategy === 'swing' ? SWING_DIMS : SHORT_DIMS
})

/**
 * 预计算每个维度的可展示详情项
 */
const displayItemsMap = computed(() => {
  const map: Record<string, { key: string; label: string; value: string }[]> = {}
  for (const dim of allDims.value) {
    const detail = props.scoreDetails?.[dim]
    if (!detail || Object.keys(detail).length === 0) {
      map[dim] = []
      continue
    }
    map[dim] = Object.entries(detail)
      .filter(([k]) => !HIDDEN_KEYS.has(k))
      .map(([k, v]) => ({
        key: k,
        label: detailLabel(k),
        value: formatDetailValue(v),
      }))
  }
  return map
})

/**
 * 判断 detail key 是否为加分项
 */
function isBonus(key: string): boolean {
  return key.includes('bonus')
}

/**
 * 判断 detail key 是否为扣分项
 */
function isPenalty(key: string): boolean {
  return key.includes('penalty')
}

function toggleDetail(dim: string) {
  expandedDim.value = expandedDim.value === dim ? null : dim
}
</script>

<template>
  <!-- 遍历各维度，渲染标签 + 进度条 + 详情 -->
  <div class="space-y-2">
    <div v-for="dim in allDims" :key="dim">
      <!-- 维度名称 + 得分 + 详情切换按钮 -->
      <div class="flex items-center justify-between text-xs text-gray-600 mb-1">
        <span>{{ dimLabel(dim) }}</span>
        <div class="flex items-center gap-1">
          <span class="font-medium">{{ scores[dim] ?? 0 }}</span>
          <span
            v-if="displayItemsMap[dim]?.length"
            class="text-blue-500 cursor-pointer text-[10px]"
            @click="toggleDetail(dim)"
          >
            {{ expandedDim === dim ? '收起' : '详情' }}
          </span>
        </div>
      </div>
      <!-- Vant 进度条：百分比即得分（得分范围 0-100） -->
      <van-progress :percentage="scores[dim] ?? 0" :show-pivot="false" stroke-width="6" />

      <!-- 打分详情（展开时显示） -->
      <div v-if="expandedDim === dim && displayItemsMap[dim]?.length" class="detail-grid mt-1">
        <div
          v-for="item in displayItemsMap[dim]"
          :key="item.key"
          class="detail-item"
          :class="{
            'text-green-600': isBonus(item.key),
            'text-red-500': isPenalty(item.key),
          }"
        >
          <span class="detail-label">{{ item.label }}</span>
          <span class="detail-value">{{ item.value }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 8px;
  padding: 4px 0;
}

.detail-item {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  line-height: 1.6;
  color: #4b5563;
}

.detail-label {
  color: #9ca3af;
  flex-shrink: 0;
}

.detail-value {
  font-weight: 500;
  text-align: right;
}
</style>
