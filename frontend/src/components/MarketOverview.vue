<template>
  <div class="flex flex-wrap gap-4 rounded-lg bg-white p-4 shadow">
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">上证综指</div>
      <div class="text-lg font-bold">{{ market.sh_index.close.toFixed(0) }}</div>
      <div :class="pctClass(market.sh_index.pct_chg)">
        {{ fmtPct(market.sh_index.pct_chg) }}
      </div>
    </div>
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">沪深 300</div>
      <div class="text-lg font-bold">{{ market.hs300_index.close.toFixed(0) }}</div>
      <div :class="pctClass(market.hs300_index.pct_chg)">
        {{ fmtPct(market.hs300_index.pct_chg) }}
      </div>
    </div>
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">Regime</div>
      <div class="text-lg font-bold">{{ market.regime_score }}</div>
      <div class="text-xs text-gray-400">大盘环境分</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MarketData } from '../api'

defineProps<{ market: MarketData }>()

function fmtPct(v: number) {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function pctClass(v: number) {
  return v >= 0 ? 'text-sm text-red-600' : 'text-sm text-green-600'
}
</script>
