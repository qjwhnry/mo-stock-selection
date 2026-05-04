<script setup lang="ts">
/**
 * 市场概况组件
 *
 * 功能：以三列网格形式展示当日市场关键指数：
 * 1. 上证综指：收盘点位 + 涨跌幅（红涨绿跌）
 * 2. 沪深 300：收盘点位 + 涨跌幅
 * 3. Regime 评分：大盘环境评分（主要用于 swing 策略的组合层控制）
 */

import type { MarketData } from '../api'

// 从父组件接收市场数据
defineProps<{ market: MarketData }>()

/**
 * 格式化涨跌幅显示：保留两位小数并带正负号
 */
function fmtPct(v: number) {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

/**
 * 根据涨跌幅返回 Tailwind 文本颜色类名
 * 正数（上涨）-> 红色，负数（下跌）-> 绿色
 */
function pctClass(v: number) {
  return v >= 0 ? 'text-sm text-red-600' : 'text-sm text-green-600'
}
</script>

<template>
  <!-- 三列网格布局 -->
  <van-cell-group inset>
    <van-grid :column-num="3" :border="false">

      <!-- 上证综指 -->
      <van-grid-item>
        <template #text>
          <div class="text-center">
            <div class="text-xs text-gray-500">上证综指</div>
            <div class="text-lg font-bold">{{ market.sh_index.close.toFixed(0) }}</div>
            <div :class="pctClass(market.sh_index.pct_chg)">
              {{ fmtPct(market.sh_index.pct_chg) }}
            </div>
          </div>
        </template>
      </van-grid-item>

      <!-- 沪深 300 -->
      <van-grid-item>
        <template #text>
          <div class="text-center">
            <div class="text-xs text-gray-500">沪深 300</div>
            <div class="text-lg font-bold">{{ market.hs300_index.close.toFixed(0) }}</div>
            <div :class="pctClass(market.hs300_index.pct_chg)">
              {{ fmtPct(market.hs300_index.pct_chg) }}
            </div>
          </div>
        </template>
      </van-grid-item>

      <!-- 大盘环境评分（Regime Score） -->
      <van-grid-item>
        <template #text>
          <div class="text-center">
            <div class="text-xs text-gray-500">Regime</div>
            <div class="text-lg font-bold">{{ market.regime_score }}</div>
            <div class="text-xs text-gray-400">大盘环境分</div>
          </div>
        </template>
      </van-grid-item>

    </van-grid>
  </van-cell-group>
</template>
