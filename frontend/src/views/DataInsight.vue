<script setup lang="ts">
/**
 * 数据洞察页
 *
 * 按交易日只读查看数据库内的资金流、龙虎榜和席位明细。
 */

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  dimLabel,
  fetchDataSectors,
  fetchLhbSeats,
  fetchLhbSummary,
  fetchMoneyflowSummary,
  type LhbSeatItem,
  type LhbSummaryResponse,
  type MoneyflowSummaryResponse,
} from '../api'

type DataKind = 'moneyflow' | 'lhb'

const router = useRouter()

const pageSize = 20
const today = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date())

const filters = reactive({
  tradeDate: today,
  strategy: 'short',
  kind: 'moneyflow' as DataKind,
  keyword: '',
  sector: '',
  sortBy: 'net_mf_ratio_pct',
  order: 'desc' as 'asc' | 'desc',
})

const page = ref(1)
const sectors = ref<string[]>([])
const moneyflowData = ref<MoneyflowSummaryResponse | null>(null)
const lhbData = ref<LhbSummaryResponse | null>(null)
const loading = ref(false)
const error = ref('')
const showDatePicker = ref(false)
const datePickerValue = ref(filters.tradeDate.split('-'))
const showSeats = ref(false)
const seatsLoading = ref(false)
const currentSeatsTitle = ref('')
const currentSeats = ref<LhbSeatItem[]>([])

const total = computed(() => (
  filters.kind === 'moneyflow'
    ? moneyflowData.value?.total || 0
    : lhbData.value?.total || 0
))

const sortOptions = computed(() => (
  filters.kind === 'moneyflow'
    ? [
        { text: '净流入占比', value: 'net_mf_ratio_pct' },
        { text: '主力净流入', value: 'net_mf_wan' },
        { text: '涨跌幅', value: 'pct_chg' },
        { text: '最终分', value: 'final_score' },
        { text: '规则分', value: 'rule_score' },
      ]
    : [
        { text: '龙虎榜净买占比', value: 'lhb_net_rate_pct' },
        { text: '龙虎榜净买额', value: 'lhb_net_amount_wan' },
        { text: '涨跌幅', value: 'pct_chg' },
        { text: '最终分', value: 'final_score' },
        { text: '规则分', value: 'rule_score' },
      ]
))

function fmt(value: number | null | undefined, suffix = '') {
  if (value === null || value === undefined) return '暂无'
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${suffix}`
}

function scoreText(scores: Record<string, number>) {
  const entries = Object.entries(scores)
  if (!entries.length) return '暂无维度分'
  return entries.map(([key, value]) => `${dimLabel(key)} ${value}`).join(' · ')
}

function resetPageAndLoad() {
  page.value = 1
  loadData()
}

async function loadSectors() {
  try {
    const { data } = await fetchDataSectors(filters.tradeDate)
    sectors.value = data.sectors
    if (filters.sector && !data.sectors.includes(filters.sector)) {
      filters.sector = ''
    }
  } catch {
    sectors.value = []
  }
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    if (filters.kind === 'moneyflow') {
      const { data } = await fetchMoneyflowSummary({
        tradeDate: filters.tradeDate,
        strategy: filters.strategy,
        keyword: filters.keyword || undefined,
        sector: filters.sector || undefined,
        sortBy: filters.sortBy,
        order: filters.order,
        page: page.value,
        pageSize,
      })
      moneyflowData.value = data
    } else {
      const { data } = await fetchLhbSummary({
        tradeDate: filters.tradeDate,
        strategy: filters.strategy,
        keyword: filters.keyword || undefined,
        sector: filters.sector || undefined,
        sortBy: filters.sortBy,
        order: filters.order,
        page: page.value,
        pageSize,
      })
      lhbData.value = data
    }
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

function onKindChange() {
  filters.sortBy = filters.kind === 'moneyflow' ? 'net_mf_ratio_pct' : 'lhb_net_rate_pct'
  page.value = 1
  loadData()
}

async function onDateConfirm({ selectedValues }: { selectedValues: string[] }) {
  filters.tradeDate = selectedValues.join('-')
  showDatePicker.value = false
  await loadSectors()
  resetPageAndLoad()
}

async function openSeats(tsCode: string, name: string) {
  showSeats.value = true
  seatsLoading.value = true
  currentSeatsTitle.value = `${tsCode} ${name}`
  currentSeats.value = []
  try {
    const { data } = await fetchLhbSeats(tsCode, filters.tradeDate)
    currentSeats.value = data.seats
  } catch {
    currentSeats.value = []
  } finally {
    seatsLoading.value = false
  }
}

function goStock(tsCode: string) {
  router.push(`/stock/${tsCode}?strategy=${filters.strategy}`)
}

watch(() => filters.kind, onKindChange)

onMounted(async () => {
  await loadSectors()
  await loadData()
})
</script>

<template>
  <div class="min-h-screen bg-gray-50">
    <van-nav-bar
      title="数据洞察"
      left-text="返回"
      left-arrow
      @click-left="$router.back()"
    />

    <van-tabs v-model:active="filters.strategy" @change="resetPageAndLoad">
      <van-tab title="短线" name="short" />
      <van-tab title="波段" name="swing" />
    </van-tabs>

    <div class="px-3 py-4 space-y-3">
      <van-cell-group inset>
        <van-field
          :model-value="filters.tradeDate"
          label="交易日"
          readonly
          is-link
          @click="showDatePicker = true"
        />
        <van-field
          v-model="filters.keyword"
          label="搜索"
          placeholder="股票代码或名称，按回车搜索"
          clearable
          @keyup.enter="resetPageAndLoad"
          @clear="resetPageAndLoad"
        />
        <van-cell title="行业">
          <template #value>
            <select v-model="filters.sector" class="filter-select" @change="resetPageAndLoad">
              <option value="">全部行业</option>
              <option v-for="sector in sectors" :key="sector" :value="sector">{{ sector }}</option>
            </select>
          </template>
        </van-cell>
        <van-cell title="排序">
          <template #value>
            <div class="inline-controls">
              <select v-model="filters.sortBy" class="filter-select" @change="resetPageAndLoad">
                <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
              </select>
              <select v-model="filters.order" class="order-select" @change="resetPageAndLoad">
                <option value="desc">降序</option>
                <option value="asc">升序</option>
              </select>
            </div>
          </template>
        </van-cell>
      </van-cell-group>

      <van-tabs v-model:active="filters.kind">
        <van-tab title="资金流" name="moneyflow" />
        <van-tab title="龙虎榜" name="lhb" />
      </van-tabs>

      <div v-if="filters.kind === 'moneyflow' && moneyflowData" class="summary-grid">
        <div class="summary-box">
          <span>净流入股票</span>
          <strong>{{ moneyflowData.summary.net_mf_positive_count }}</strong>
        </div>
        <div class="summary-box">
          <span>合计净流入</span>
          <strong>{{ fmt(moneyflowData.summary.total_net_mf_wan, ' 万') }}</strong>
        </div>
      </div>

      <div v-if="filters.kind === 'lhb' && lhbData" class="summary-grid">
        <div class="summary-box">
          <span>上榜股票</span>
          <strong>{{ lhbData.summary.lhb_count }}</strong>
        </div>
        <div class="summary-box">
          <span>机构净买</span>
          <strong>{{ lhbData.summary.institution_net_buy_count }}</strong>
        </div>
        <div class="summary-box wide">
          <span>龙虎榜净买</span>
          <strong>{{ fmt(lhbData.summary.total_lhb_net_amount_wan, ' 万') }}</strong>
        </div>
      </div>

      <div v-if="loading" class="py-12 text-center">
        <van-loading size="24px">加载中...</van-loading>
      </div>
      <van-empty v-else-if="error" :description="error" />

      <template v-else>
        <van-empty
          v-if="filters.kind === 'moneyflow' && !moneyflowData?.items.length"
          description="暂无资金流数据"
        />
        <van-cell-group v-else-if="filters.kind === 'moneyflow' && moneyflowData" inset>
          <van-cell v-for="item in moneyflowData.items" :key="item.ts_code">
            <template #title>
              <div class="stock-title" @click="goStock(item.ts_code)">
                <strong>{{ item.name }}</strong>
                <span>{{ item.ts_code }}</span>
                <van-tag v-if="item.picked" type="primary">入选</van-tag>
              </div>
            </template>
            <template #label>
              <div class="item-lines">
                <span>{{ item.industry || '暂无行业' }} · 收盘 {{ fmt(item.close) }} · 涨跌 {{ fmt(item.pct_chg, '%') }}</span>
                <span>主力净流入 {{ fmt(item.net_mf_wan, ' 万') }} · 占成交额 {{ fmt(item.net_mf_ratio_pct, '%') }}</span>
                <span>大单 {{ fmt(item.buy_lg_wan, ' 万') }}/{{ fmt(item.sell_lg_wan, ' 万') }} · 超大单 {{ fmt(item.buy_elg_wan, ' 万') }}/{{ fmt(item.sell_elg_wan, ' 万') }}</span>
                <span>最终分 {{ fmt(item.final_score) }} · 规则分 {{ fmt(item.rule_score) }} · {{ scoreText(item.scores) }}</span>
              </div>
            </template>
          </van-cell>
        </van-cell-group>

        <van-empty
          v-if="filters.kind === 'lhb' && !lhbData?.items.length"
          description="暂无龙虎榜数据"
        />
        <van-cell-group v-else-if="filters.kind === 'lhb' && lhbData" inset>
          <van-cell v-for="item in lhbData.items" :key="item.ts_code">
            <template #title>
              <div class="stock-title" @click="goStock(item.ts_code)">
                <strong>{{ item.name }}</strong>
                <span>{{ item.ts_code }}</span>
                <van-tag v-if="item.picked" type="primary">入选</van-tag>
              </div>
            </template>
            <template #label>
              <div class="item-lines">
                <span>{{ item.industry || '暂无行业' }} · 收盘 {{ fmt(item.close) }} · 涨跌 {{ fmt(item.pct_chg, '%') }}</span>
                <span>净买 {{ fmt(item.lhb_net_amount_wan, ' 万') }} · 净买占比 {{ fmt(item.lhb_net_rate_pct, '%') }} · 成交占比 {{ fmt(item.lhb_amount_rate_pct, '%') }}</span>
                <span>买入 {{ fmt(item.lhb_buy_wan, ' 万') }} · 卖出 {{ fmt(item.lhb_sell_wan, ' 万') }} · 席位 {{ Object.entries(item.seat_summary).map(([k, v]) => `${k}:${v}`).join(' ') || '暂无' }}</span>
                <span>{{ item.reason || '暂无上榜原因' }}</span>
                <span>最终分 {{ fmt(item.final_score) }} · 规则分 {{ fmt(item.rule_score) }} · {{ scoreText(item.scores) }}</span>
                <van-button size="small" plain type="primary" @click="openSeats(item.ts_code, item.name)">查看席位明细</van-button>
              </div>
            </template>
          </van-cell>
        </van-cell-group>
      </template>

      <van-pagination
        v-if="total > pageSize"
        v-model="page"
        :total-items="total"
        :items-per-page="pageSize"
        @change="loadData"
      />
    </div>

    <van-popup v-model:show="showDatePicker" position="bottom">
      <van-date-picker
        v-model="datePickerValue"
        title="选择交易日"
        @confirm="onDateConfirm"
        @cancel="showDatePicker = false"
      />
    </van-popup>

    <van-popup v-model:show="showSeats" position="bottom" round :style="{ height: '70%' }">
      <div class="p-4">
        <div class="popup-title">{{ currentSeatsTitle }} 席位明细</div>
        <div v-if="seatsLoading" class="py-8 text-center">
          <van-loading size="24px">加载中...</van-loading>
        </div>
        <van-empty v-else-if="currentSeats.length === 0" description="暂无席位明细" />
        <van-cell-group v-else inset>
          <van-cell v-for="seat in currentSeats" :key="seat.seat_no">
            <template #title>
              <div class="stock-title">
                <strong>{{ seat.seat_no }}. {{ seat.exalter || '未知席位' }}</strong>
                <van-tag>{{ seat.seat_type }}</van-tag>
              </div>
            </template>
            <template #label>
              买入 {{ fmt(seat.buy_wan, ' 万') }} · 卖出 {{ fmt(seat.sell_wan, ' 万') }} · 净买 {{ fmt(seat.net_buy_wan, ' 万') }}
            </template>
          </van-cell>
        </van-cell-group>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.filter-select,
.order-select {
  max-width: 130px;
  border: 0;
  background: transparent;
  color: #2563eb;
  text-align: right;
}

.inline-controls {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.summary-box {
  min-height: 72px;
  padding: 12px;
  border: 1px solid rgba(31, 42, 37, 0.08);
  border-radius: 8px;
  background: #fff;
}

.summary-box.wide {
  grid-column: 1 / -1;
}

.summary-box span {
  display: block;
  color: #6b7280;
  font-size: 12px;
}

.summary-box strong {
  display: block;
  margin-top: 6px;
  color: #111827;
  font-size: 18px;
}

.stock-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.stock-title span {
  color: #6b7280;
  font-size: 12px;
}

.item-lines {
  display: grid;
  gap: 4px;
  margin-top: 6px;
  color: #4b5563;
  line-height: 1.5;
}

.popup-title {
  margin-bottom: 12px;
  color: #111827;
  font-weight: 700;
}
</style>
