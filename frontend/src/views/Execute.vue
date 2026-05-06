<script setup lang="ts">
/**
 * 执行页面：数据同步、选股执行、定时调度。
 */

import { onMounted, onUnmounted, ref, reactive, computed } from 'vue'
import {
  runTask,
  runSyncOneDay,
  runBackfill,
  runRefreshIndex,
  runRefreshMetadata,
  fetchTaskStatus,
  startScheduler,
  stopScheduler,
  fetchSchedulerStatus,
  type TaskStatusResponse,
  type SchedulerStatusResponse,
} from '../api'

type DateTarget =
  | 'syncTradeDate'
  | 'backfillEndDate'
  | 'refreshIndexEndDate'
  | 'calendarStart'
  | 'calendarEnd'
  | 'runTradeDate'

const activeTab = ref('sync')

const syncForm = reactive({
  tradeDate: '',
  skipEnhanced: false,
})

const backfillForm = reactive({
  days: 180,
  endDate: '',
})

const refreshIndexForm = reactive({
  days: 180,
  endDate: '',
})

const metadataForm = reactive({
  refreshBasics: true,
  withThs: false,
  withHmList: true,
  refreshCalendar: false,
  calendarStart: '',
  calendarEnd: '',
})

const runForm = reactive({
  strategy: 'short',
  tradeDate: '',
  skipAi: false,
})

const schedForm = reactive({
  strategy: 'short',
  cronHour: 15,
  cronMinute: 30,
  skipAi: false,
})

const taskStatus = ref<TaskStatusResponse | null>(null)
const schedStatus = ref<SchedulerStatusResponse | null>(null)

const showStrategyPicker = ref(false)
const showSchedStrategyPicker = ref(false)
const showDatePicker = ref(false)
const showTimePicker = ref(false)
const activeDateTarget = ref<DateTarget>('syncTradeDate')

function todayText(): string {
  return todayPickerValue().join('-')
}

function todayPickerValue(): string[] {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const getPart = (type: string) => parts.find(part => part.type === type)?.value || ''
  return [getPart('year'), getPart('month'), getPart('day')]
}

function plusDaysText(days: number): string {
  const now = new Date()
  now.setDate(now.getDate() + days)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const getPart = (type: string) => parts.find(part => part.type === type)?.value || ''
  return [getPart('year'), getPart('month'), getPart('day')].join('-')
}

const datePickerValue = ref(todayPickerValue())

const timeColumns = computed(() => [
  Array.from({ length: 24 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
  Array.from({ length: 60 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
])

const isTaskRunning = computed(() => taskStatus.value?.status === 'running')

const statsEntries = computed(() => Object.entries(taskStatus.value?.stats || {}))

function openDatePicker(target: DateTarget, value?: string) {
  activeDateTarget.value = target
  datePickerValue.value = value ? value.split('-') : todayPickerValue()
  showDatePicker.value = true
}

function onDateConfirm({ selectedValues }: { selectedValues: string[] }) {
  const value = selectedValues.join('-')
  if (activeDateTarget.value === 'syncTradeDate') syncForm.tradeDate = value
  if (activeDateTarget.value === 'backfillEndDate') backfillForm.endDate = value
  if (activeDateTarget.value === 'refreshIndexEndDate') refreshIndexForm.endDate = value
  if (activeDateTarget.value === 'calendarStart') metadataForm.calendarStart = value
  if (activeDateTarget.value === 'calendarEnd') metadataForm.calendarEnd = value
  if (activeDateTarget.value === 'runTradeDate') runForm.tradeDate = value
  showDatePicker.value = false
}

function onTimeConfirm({ selectedValues }: { selectedValues: number[] }) {
  schedForm.cronHour = selectedValues[0]
  schedForm.cronMinute = selectedValues[1]
  showTimePicker.value = false
}

function strategyText(strategy?: string | null): string {
  if (strategy === 'short') return '短线'
  if (strategy === 'swing') return '波段'
  return '-'
}

function taskTypeText(type?: string | null): string {
  const labels: Record<string, string> = {
    sync_one_day: '单日同步',
    backfill: '历史回填',
    refresh_index: '指数补齐',
    refresh_metadata: '元数据刷新',
    run_pipeline: '选股执行',
  }
  return type ? labels[type] || type : '-'
}

function statLabel(key: string): string {
  const labels: Record<string, string> = {
    stock_basic: '股票基础',
    index_member: '申万行业映射',
    trade_cal: '交易日历',
    daily_kline: '个股日线',
    index_daily: '指数日线',
    daily_basic: '日基础指标',
    limit_list: '涨停数据',
    moneyflow: '主力资金流',
    lhb: '龙虎榜',
    sw_daily: '申万板块日线',
    ths_daily: '同花顺行情',
    limit_concept: '涨停概念',
    concept_moneyflow: '概念资金流',
    top_inst: '席位明细',
    hm_detail: '游资明细',
    ths_index: '同花顺概念',
    ths_member: '概念成分',
    hot_money_list: '游资名录',
  }
  return labels[key] ? `${labels[key]} (${key})` : key
}

function cronFriendly(cron: string): string {
  const parts = cron.split(/\s+/)
  if (parts.length !== 5) return cron
  const [min, hour, , , dow] = parts
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
  if (dow === 'mon-fri' || dow === '1-5') return `工作日 ${time}`
  if (dow === '*') return `每天 ${time}`
  return cron
}

function assertDays(days: number, max: number, label: string): boolean {
  if (!Number.isFinite(days) || days < 1 || days > max) {
    alert(`${label}天数需在 1-${max} 之间`)
    return false
  }
  if (days > 180 && !window.confirm(`${label}超过 180 天，任务可能运行较久，确认继续？`)) {
    return false
  }
  return true
}

async function handleSyncOneDay() {
  try {
    const { data } = await runSyncOneDay({
      tradeDate: syncForm.tradeDate || undefined,
      skipEnhanced: syncForm.skipEnhanced,
    })
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '同步失败')
  }
}

async function handleBackfill() {
  if (!assertDays(Number(backfillForm.days), 730, '回填')) return
  try {
    const { data } = await runBackfill({
      days: Number(backfillForm.days),
      endDate: backfillForm.endDate || undefined,
    })
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '回填失败')
  }
}

async function handleRefreshIndex() {
  if (!assertDays(Number(refreshIndexForm.days), 1825, '指数补齐')) return
  try {
    const { data } = await runRefreshIndex({
      days: Number(refreshIndexForm.days),
      endDate: refreshIndexForm.endDate || undefined,
    })
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '指数补齐失败')
  }
}

async function handleRefreshMetadata() {
  if (!metadataForm.refreshBasics && !metadataForm.withThs && !metadataForm.withHmList && !metadataForm.refreshCalendar) {
    alert('至少选择一个刷新项')
    return
  }
  if (metadataForm.refreshCalendar && !metadataForm.calendarStart) {
    alert('刷新交易日历时需要选择开始日期')
    return
  }
  try {
    const { data } = await runRefreshMetadata({
      refreshBasics: metadataForm.refreshBasics,
      withThs: metadataForm.withThs,
      withHmList: metadataForm.withHmList,
      refreshCalendar: metadataForm.refreshCalendar,
      calendarStart: metadataForm.calendarStart || undefined,
      calendarEnd: metadataForm.calendarEnd || undefined,
    })
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '元数据刷新失败')
  }
}

async function handleRun() {
  try {
    const { data } = await runTask(runForm.strategy, runForm.tradeDate || undefined, runForm.skipAi)
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '执行失败')
  }
}

async function handleStartSched() {
  try {
    await startScheduler(schedForm.strategy, schedForm.skipAi, schedForm.cronHour, schedForm.cronMinute)
    await pollStatus()
  } catch (e: any) {
    alert(e.response?.data?.detail || '启动失败')
  }
}

async function handleStopSched() {
  try {
    await stopScheduler()
    await pollStatus()
  } catch (e: any) {
    alert(e.response?.data?.detail || '停止失败')
  }
}

async function pollStatus() {
  try {
    const [task, sched] = await Promise.all([fetchTaskStatus(), fetchSchedulerStatus()])
    taskStatus.value = task.data
    schedStatus.value = sched.data
  } catch {
    // 状态轮询失败时保持当前页面状态。
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (!metadataForm.calendarStart) metadataForm.calendarStart = `${todayText().slice(0, 4)}-01-01`
  if (!metadataForm.calendarEnd) metadataForm.calendarEnd = plusDaysText(365)
  pollStatus()
  pollTimer = setInterval(pollStatus, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="min-h-screen bg-gray-50">
    <van-nav-bar
      title="任务执行"
      left-text="返回"
      left-arrow
      @click-left="$router.push('/')"
    />

    <div class="px-3 py-4 space-y-4">
      <van-tabs v-model:active="activeTab" shrink>
        <van-tab title="数据同步" name="sync">
          <div class="py-3 space-y-4">
            <van-cell-group inset title="单日同步">
              <van-field
                :model-value="syncForm.tradeDate"
                is-link
                readonly
                label="交易日期"
                placeholder="默认当天"
                @click="openDatePicker('syncTradeDate', syncForm.tradeDate)"
              />
              <van-cell title="只跑核心数据">
                <template #value>
                  <van-switch v-model="syncForm.skipEnhanced" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <div class="p-3">
                <van-button type="primary" block :disabled="isTaskRunning" @click="handleSyncOneDay">
                  开始单日同步
                </van-button>
              </div>
            </van-cell-group>

            <van-cell-group inset title="历史回填">
              <van-field v-model.number="backfillForm.days" type="number" label="回填天数" />
              <van-field
                :model-value="backfillForm.endDate"
                is-link
                readonly
                label="截止日期"
                placeholder="默认当天"
                @click="openDatePicker('backfillEndDate', backfillForm.endDate)"
              />
              <div class="p-3">
                <van-button type="warning" block :disabled="isTaskRunning" @click="handleBackfill">
                  开始历史回填
                </van-button>
              </div>
            </van-cell-group>

            <van-cell-group inset title="指数补齐">
              <van-field v-model.number="refreshIndexForm.days" type="number" label="补齐天数" />
              <van-field
                :model-value="refreshIndexForm.endDate"
                is-link
                readonly
                label="截止日期"
                placeholder="默认当天"
                @click="openDatePicker('refreshIndexEndDate', refreshIndexForm.endDate)"
              />
              <div class="p-3">
                <van-button type="success" block :disabled="isTaskRunning" @click="handleRefreshIndex">
                  补齐指数日线
                </van-button>
              </div>
            </van-cell-group>

            <van-cell-group inset title="元数据刷新">
              <van-cell title="股票基础/申万行业">
                <template #value>
                  <van-switch v-model="metadataForm.refreshBasics" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <van-cell title="同花顺概念">
                <template #value>
                  <van-switch v-model="metadataForm.withThs" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <van-cell title="游资名录">
                <template #value>
                  <van-switch v-model="metadataForm.withHmList" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <van-cell title="交易日历">
                <template #value>
                  <van-switch v-model="metadataForm.refreshCalendar" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <van-field
                v-if="metadataForm.refreshCalendar"
                :model-value="metadataForm.calendarStart"
                is-link
                readonly
                label="日历开始"
                @click="openDatePicker('calendarStart', metadataForm.calendarStart)"
              />
              <van-field
                v-if="metadataForm.refreshCalendar"
                :model-value="metadataForm.calendarEnd"
                is-link
                readonly
                label="日历结束"
                @click="openDatePicker('calendarEnd', metadataForm.calendarEnd)"
              />
              <div class="p-3">
                <van-button type="primary" block plain :disabled="isTaskRunning" @click="handleRefreshMetadata">
                  刷新元数据
                </van-button>
              </div>
            </van-cell-group>
          </div>
        </van-tab>

        <van-tab title="选股执行" name="run">
          <div class="py-3">
            <van-cell-group inset title="完整选股流程">
              <van-field
                :model-value="strategyText(runForm.strategy)"
                is-link
                readonly
                label="策略"
                @click="showStrategyPicker = true"
              />
              <van-field
                :model-value="runForm.tradeDate"
                is-link
                readonly
                label="日期"
                placeholder="默认当天"
                @click="openDatePicker('runTradeDate', runForm.tradeDate)"
              />
              <van-cell title="跳过 AI">
                <template #value>
                  <van-switch v-model="runForm.skipAi" size="20px" :disabled="isTaskRunning" />
                </template>
              </van-cell>
              <div class="p-3">
                <van-button type="primary" block :disabled="isTaskRunning" @click="handleRun">
                  执行选股
                </van-button>
              </div>
            </van-cell-group>
          </div>
        </van-tab>

        <van-tab title="定时调度" name="scheduler">
          <div class="py-3 space-y-4">
            <van-cell-group inset title="定时调度">
              <van-field
                :model-value="strategyText(schedForm.strategy)"
                is-link
                readonly
                label="策略"
                :disabled="schedStatus?.status === 'running'"
                @click="schedStatus?.status !== 'running' && (showSchedStrategyPicker = true)"
              />
              <van-field
                :model-value="`${String(schedForm.cronHour).padStart(2, '0')}:${String(schedForm.cronMinute).padStart(2, '0')}`"
                is-link
                readonly
                label="执行时间"
                :disabled="schedStatus?.status === 'running'"
                @click="schedStatus?.status !== 'running' && (showTimePicker = true)"
              />
              <van-cell title="跳过 AI">
                <template #value>
                  <van-switch v-model="schedForm.skipAi" size="20px" :disabled="schedStatus?.status === 'running'" />
                </template>
              </van-cell>
              <div class="p-3">
                <van-button
                  v-if="schedStatus?.status !== 'running'"
                  type="success"
                  block
                  @click="handleStartSched"
                >
                  启动调度
                </van-button>
                <van-button v-else type="danger" block @click="handleStopSched">
                  停止调度
                </van-button>
              </div>
            </van-cell-group>

            <van-cell-group v-if="schedStatus?.status === 'running'" inset>
              <van-cell title="状态" value="运行中" />
              <van-cell title="策略" :value="strategyText(schedStatus.strategy)" />
              <van-cell title="执行规则" :value="cronFriendly(schedStatus.cron || '')" />
              <van-cell v-if="schedStatus.next_run" title="下次执行" :value="schedStatus.next_run" />
            </van-cell-group>
            <van-empty v-else-if="schedStatus" description="调度器未启动" :image-size="60" />
          </div>
        </van-tab>
      </van-tabs>

      <van-notice-bar
        v-if="taskStatus?.status === 'running'"
        left-text="正在执行"
        :text="taskStatus.message || `${taskTypeText(taskStatus.task_type)} · ${taskStatus.started_at || ''}`"
      />
      <van-notice-bar
        v-else-if="taskStatus?.status === 'error'"
        color="#ee0a24"
        background="#ffe1e1"
        left-text="执行失败"
        :text="taskStatus.error || taskStatus.message || ''"
      />
      <van-notice-bar
        v-else-if="taskStatus?.status === 'success'"
        left-text="执行完成"
        :text="taskStatus.message || taskTypeText(taskStatus.task_type)"
      />

      <van-cell-group v-if="taskStatus && taskStatus.status !== 'idle'" inset title="任务状态">
        <van-cell title="任务类型" :value="taskTypeText(taskStatus.task_type)" />
        <van-cell title="状态" :value="taskStatus.status" />
        <van-cell v-if="taskStatus.strategy" title="策略" :value="strategyText(taskStatus.strategy)" />
        <van-cell v-if="taskStatus.trade_date" title="交易日期" :value="taskStatus.trade_date" />
        <van-cell v-if="taskStatus.started_at" title="开始时间" :value="taskStatus.started_at" />
        <van-cell v-if="taskStatus.finished_at" title="结束时间" :value="taskStatus.finished_at" />
      </van-cell-group>

      <van-cell-group v-if="statsEntries.length > 0" inset title="执行结果">
        <van-cell v-for="[key, value] in statsEntries" :key="key" :title="statLabel(key)">
          <template #value>
            <span :class="value < 0 ? 'text-red-600' : ''">
              {{ value < 0 ? '失败' : `${value} 行` }}
            </span>
          </template>
        </van-cell>
      </van-cell-group>
    </div>

    <van-popup v-model:show="showStrategyPicker" position="bottom" round>
      <van-picker
        :columns="[{ text: '短线', value: 'short' }, { text: '波段', value: 'swing' }]"
        @confirm="({ selectedValues }: any) => { runForm.strategy = selectedValues[0]; showStrategyPicker = false }"
        @cancel="showStrategyPicker = false"
      />
    </van-popup>

    <van-popup v-model:show="showSchedStrategyPicker" position="bottom" round>
      <van-picker
        :columns="[{ text: '短线', value: 'short' }, { text: '波段', value: 'swing' }]"
        @confirm="({ selectedValues }: any) => { schedForm.strategy = selectedValues[0]; showSchedStrategyPicker = false }"
        @cancel="showSchedStrategyPicker = false"
      />
    </van-popup>

    <van-popup v-model:show="showDatePicker" position="bottom" round>
      <van-date-picker
        v-model="datePickerValue"
        @confirm="onDateConfirm"
        @cancel="showDatePicker = false"
      />
    </van-popup>

    <van-popup v-model:show="showTimePicker" position="bottom" round>
      <van-picker
        :columns="timeColumns"
        @confirm="onTimeConfirm"
        @cancel="showTimePicker = false"
      />
    </van-popup>
  </div>
</template>
