<script setup lang="ts">
/**
 * 股票列表（可折叠）组件
 *
 * 功能：
 * 1. 展示入选股票的简要信息列表（名称、代码、行业、综合分、排名）
 * 2. 点击股票卡片可展开，查看各维度评分柱状图和 AI 摘要
 * 3. 支持排序：按综合分、规则分、AI 分，或按各维度排序
 * 4. 支持升序/降序切换
 * 5. 点击"查看详情"跳转到个股详情页
 */

import { ref, computed } from 'vue'
import type { StockItem } from '../api'
import { dimLabel } from '../api'
import DimensionBar from './DimensionBar.vue'
import AiSummary from './AiSummary.vue'

const props = defineProps<{
  stocks: StockItem[]       // 股票列表数据
  strategy: string           // 当前策略（short/swing）
  tradeDate: string          // 当前报告日期
  currentSort: string       // 当前排序列名
  currentOrder: string      // 当前排序方向（desc/asc）
  showSort?: boolean        // 是否显示排序控件，默认 true
}>()

// 向父组件发射排序变化事件
const emit = defineEmits<{
  sort: [sortBy: string, order: string]
}>()

// 当前展开的股票折叠面板名称列表
const expandedNames = ref<string[]>([])

// 排序选择器弹窗控制
const showSortPicker = ref(false)

/**
 * 排序选项：
 * - 基础选项：综合分、规则分、AI 分
 * - 维度选项：根据策略类型动态添加对应维度
 */
const sortOptions = computed(() => {
  const base = [
    { text: '综合分', value: 'final_score' },
    { text: '规则分', value: 'rule_score' },
    { text: 'AI 分', value: 'ai_score' },
  ]
  const dims = props.strategy === 'swing'
    ? ['trend', 'pullback', 'moneyflow_swing', 'sector_swing', 'theme_swing', 'catalyst', 'risk_liquidity']
    : ['limit', 'limit_restart', 'moneyflow', 'lhb', 'sector', 'theme']
  const dimOpts = dims.map(d => ({ text: dimLabel(d), value: d }))
  return [...base, ...dimOpts]
})

/**
 * 当前排序选项的显示标签
 */
const sortLabel = computed(() => {
  const opt = sortOptions.value.find(o => o.value === props.currentSort)
  return opt?.text || props.currentSort
})

/**
 * 排序选择器确认回调：关闭弹窗并触发排序变化事件
 */
function onSortConfirm({ selectedValues }: { selectedValues: string[] }) {
  showSortPicker.value = false
  emit('sort', selectedValues[0], props.currentOrder)
}

/**
 * 切换升序/降序并触发排序变化事件
 */
function toggleOrder() {
  const newOrder = props.currentOrder === 'desc' ? 'asc' : 'desc'
  emit('sort', props.currentSort, newOrder)
}

/**
 * 根据 ts_code 生成东方财富行情K线页链接
 * 600519.SH → https://quote.eastmoney.com/concept/sh600519.html
 * 000001.SZ → https://quote.eastmoney.com/concept/sz000001.html
 * 430139.BJ → https://quote.eastmoney.com/concept/bj430139.html
 * 格式不匹配时返回 null，模板侧通过 v-if 不渲染链接
 */
function klineUrl(tsCode: string): string | null {
  const match = tsCode.match(/^(\d{6})\.(SH|SZ|BJ)$/)
  if (!match) return null

  const [, symbol, market] = match
  const prefixMap: Record<string, string> = { SH: 'sh', SZ: 'sz', BJ: 'bj' }
  return `https://quote.eastmoney.com/concept/${prefixMap[market]}${symbol}.html`
}
</script>

<template>
  <div class="space-y-3">

    <!-- 排序控制栏（仅在 showSort !== false 时显示） -->
    <van-cell-group v-if="props.showSort !== false" inset>
      <!-- 当前排序字段（点击弹出选择器） -->
      <van-field
        :model-value="sortLabel"
        is-link
        readonly
        label="排序"
        @click="showSortPicker = true"
      />
      <!-- 升序/降序切换按钮 -->
      <van-field label="方向">
        <template #input>
          <van-button size="small" @click="toggleOrder">
            {{ currentOrder === 'desc' ? '↓ 降序' : '↑ 升序' }}
          </van-button>
        </template>
      </van-field>
    </van-cell-group>

    <!-- 排序选择弹窗 -->
    <van-popup v-model:show="showSortPicker" position="bottom" round>
      <van-picker :columns="sortOptions" @confirm="onSortConfirm" @cancel="showSortPicker = false" />
    </van-popup>

    <!-- 提示文字 -->
    <p class="text-xs text-gray-400 px-2">点击股票展开查看维度评分</p>

    <!-- 股票折叠列表 -->
    <van-collapse v-model="expandedNames">
      <van-collapse-item
        v-for="stock in stocks"
        :key="stock.ts_code"
        :title="stock.name"
        :name="stock.ts_code"
      >
        <template #label>
          <div class="text-xs text-gray-400">
            <span>{{ stock.ts_code }} · {{ stock.industry }}</span>
            <div v-if="stock.concepts.length" class="mt-0.5 flex flex-wrap gap-1">
              <van-tag
                v-for="c in stock.concepts"
                :key="c"
                type="primary"
                size="medium"
                plain
              >{{ c }}</van-tag>
              <span v-if="stock.concept_count > stock.concepts.length" class="text-gray-400">+{{ stock.concept_count - stock.concepts.length }}</span>
            </div>
          </div>
        </template>
        <!-- 卡片头部右侧：综合分 + 排名 + 规则分/AI分 -->
        <template #value>
          <div class="flex flex-col items-end">
            <div class="flex items-center gap-1">
              <span class="text-blue-600 font-medium">{{ stock.final_score }}</span>
              <span class="text-gray-400 text-xs">排名{{ stock.rank }}</span>
            </div>
            <span class="text-xs text-gray-400">
              规则{{ stock.rule_score }}
              <template v-if="stock.ai_score != null"> · AI{{ stock.ai_score }}</template>
            </span>
          </div>
        </template>

        <!-- 展开内容：维度评分柱状图（含打分详情） -->
        <DimensionBar :scores="stock.scores" :score-details="stock.score_details" :strategy="strategy" />
        <!-- 展开内容：AI 摘要提示条 -->
        <AiSummary :summary="stock.ai_summary" />

        <!-- 跳转链接 -->
        <div class="mt-2 flex items-center gap-4">
          <router-link
            :to="`/stock/${stock.ts_code}?strategy=${strategy}&trade_date=${tradeDate}`"
            class="text-blue-600 text-sm"
          >
            查看详情 →
          </router-link>
          <a
            v-if="klineUrl(stock.ts_code)"
            :href="klineUrl(stock.ts_code)!"
            target="_blank"
            rel="noopener noreferrer"
            class="text-orange-500 text-sm"
          >
            K线图 ↗
          </a>
        </div>
      </van-collapse-item>
    </van-collapse>
  </div>
</template>
