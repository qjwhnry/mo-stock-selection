<script setup lang="ts">
/**
 * 执行页面
 *
 * 功能：
 * 1. 手动触发一次性选股任务（指定策略、日期、是否跳过 AI）
 * 2. 实时展示任务执行状态（轮询 /tasks/status）
 * 3. 配置并启动/停止定时调度器（每日收盘自动执行）
 * 4. 展示调度器下次执行时间
 */

import { onMounted, onUnmounted, ref, reactive, computed } from 'vue'
import {
  runTask,
  fetchTaskStatus,
  startScheduler,
  stopScheduler,
  fetchSchedulerStatus,
  type TaskStatusResponse,
  type SchedulerStatusResponse,
} from '../api'

// ============================ 手动选股表单 ============================

const runForm = reactive({
  strategy: 'short',   // 策略类型
  tradeDate: '',       // 交易日期（空=后端默认当天）
  skipAi: false,       // 是否跳过 AI 分析
})

// ============================ 定时调度表单 ============================

const schedForm = reactive({
  strategy: 'short',
  cronHour: 15,        // 定时执行小时（默认下午 3:30）
  cronMinute: 30,
  skipAi: false,
})

// ============================ 状态数据 ============================

// 当前一次性任务状态
const taskStatus = ref<TaskStatusResponse | null>(null)
// 当前调度器状态
const schedStatus = ref<SchedulerStatusResponse | null>(null)

// ============================ UI 弹窗控制 ============================

const showStrategyPicker = ref(false)       // 手动选股策略选择器
const showSchedStrategyPicker = ref(false)  // 调度策略选择器
const showDatePicker = ref(false)           // 日期选择器
const showTimePicker = ref(false)            // 时间选择器

/**
 * 日期选择器初始值：取 Asia/Shanghai 当天，避免写死日期导致后续默认值过期
 */
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

// 日期选择器初始值（年/月/日数组）
const datePickerValue = ref(todayPickerValue())

// 时间选择器列：[小时列表, 分钟列表]
const timeColumns = computed(() => [
  Array.from({ length: 24 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
  Array.from({ length: 60 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
])

// ============================ 回调处理 ============================

/**
 * 日期选择器确认：将数组拼接为 YYYY-MM-DD 格式
 */
function onDateConfirm({ selectedValues }: { selectedValues: string[] }) {
  runForm.tradeDate = selectedValues.join('-')
  showDatePicker.value = false
}

/**
 * 时间选择器确认：更新执行时间
 */
function onTimeConfirm({ selectedValues }: { selectedValues: number[] }) {
  schedForm.cronHour = selectedValues[0]
  schedForm.cronMinute = selectedValues[1]
  showTimePicker.value = false
}

/**
 * 将 Cron 表达式转换为友好文字描述
 * @param cron 5段 Cron 表达式
 */
function cronFriendly(cron: string): string {
  const parts = cron.split(/\s+/)
  if (parts.length !== 5) return cron
  const [min, hour, , , dow] = parts
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
  if (dow === 'mon-fri' || dow === '1-5') return `工作日 ${time}`
  if (dow === '*') return `每天 ${time}`
  return cron
}

// ============================ 业务操作 ============================

/**
 * 触发手动选股任务
 */
async function handleRun() {
  try {
    const { data } = await runTask(runForm.strategy, runForm.tradeDate || undefined, runForm.skipAi)
    taskStatus.value = data
  } catch (e: any) {
    alert(e.response?.data?.detail || '执行失败')
  }
}

/**
 * 启动定时调度器
 */
async function handleStartSched() {
  try {
    await startScheduler(schedForm.strategy, schedForm.skipAi, schedForm.cronHour, schedForm.cronMinute)
    await pollStatus()
  } catch (e: any) {
    alert(e.response?.data?.detail || '启动失败')
  }
}

/**
 * 停止定时调度器
 */
async function handleStopSched() {
  try {
    await stopScheduler()
    await pollStatus()
  } catch (e: any) {
    alert(e.response?.data?.detail || '停止失败')
  }
}

/**
 * 轮询获取任务状态和调度器状态
 */
async function pollStatus() {
  try {
    const [task, sched] = await Promise.all([fetchTaskStatus(), fetchSchedulerStatus()])
    taskStatus.value = task.data
    schedStatus.value = sched.data
  } catch {
    // ignore
  }
}

// ============================ 生命周期 ============================

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  pollStatus()
  // 每 3 秒轮询一次状态
  pollTimer = setInterval(pollStatus, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="min-h-screen bg-gray-50">

    <!-- 顶部导航栏 -->
    <van-nav-bar
      title="任务执行"
      left-text="返回"
      left-arrow
      @click-left="$router.push('/')"
    />

    <div class="px-3 py-4 space-y-4">

      <!-- ============================ 手动选股区域 ============================ -->
      <van-cell-group inset title="手动选股">
        <!-- 策略选择 -->
        <van-field
          :model-value="runForm.strategy === 'short' ? '短线' : '波段'"
          is-link
          readonly
          label="策略"
          @click="showStrategyPicker = true"
        />
        <!-- 日期选择 -->
        <van-field
          :model-value="runForm.tradeDate"
          is-link
          readonly
          label="日期"
          placeholder="默认当天"
          @click="showDatePicker = true"
        />
        <!-- 跳过 AI 开关 -->
        <van-cell title="跳过 AI">
          <template #value>
            <van-switch v-model="runForm.skipAi" size="20px" />
          </template>
        </van-cell>
        <!-- 执行按钮 -->
        <div class="p-3">
          <van-button
            type="primary"
            block
            :disabled="taskStatus?.status === 'running'"
            @click="handleRun"
          >
            {{ taskStatus?.status === 'running' ? '执行中...' : '执行选股' }}
          </van-button>
        </div>
      </van-cell-group>

      <!-- ============================ 任务状态提示条 ============================ -->
      <!-- 运行中 -->
      <van-notice-bar
        v-if="taskStatus && taskStatus.status === 'running'"
        left-text="正在执行"
        :text="`${taskStatus.strategy === 'short' ? '短线' : '波段'} · ${taskStatus.trade_date} · ${taskStatus.started_at}`"
      />
      <!-- 执行出错 -->
      <van-notice-bar
        v-else-if="taskStatus && taskStatus.status === 'error'"
        color="#ee0a24"
        background="#ffe1e1"
        left-text="执行失败"
        :text="taskStatus.error || ''"
      />
      <!-- 空闲完成 -->
      <van-notice-bar
        v-else-if="taskStatus && taskStatus.status === 'idle' && taskStatus.trade_date"
        left-text="执行完成"
        :text="`${taskStatus.strategy === 'short' ? '短线' : '波段'} · ${taskStatus.trade_date}`"
      />

      <!-- ============================ 定时调度区域 ============================ -->
      <van-cell-group inset title="定时调度">
        <!-- 调度策略选择（运行中禁用） -->
        <van-field
          :model-value="schedForm.strategy === 'short' ? '短线' : '波段'"
          is-link
          readonly
          label="策略"
          :disabled="schedStatus?.status === 'running'"
          @click="schedStatus?.status !== 'running' && (showSchedStrategyPicker = true)"
        />
        <!-- 执行时间选择（运行中禁用） -->
        <van-field
          :model-value="`${String(schedForm.cronHour).padStart(2, '0')}:${String(schedForm.cronMinute).padStart(2, '0')}`"
          is-link
          readonly
          label="执行时间"
          :disabled="schedStatus?.status === 'running'"
          @click="schedStatus?.status !== 'running' && (showTimePicker = true)"
        />
        <!-- 跳过 AI 开关（运行中禁用） -->
        <van-cell title="跳过 AI">
          <template #value>
            <van-switch v-model="schedForm.skipAi" size="20px" :disabled="schedStatus?.status === 'running'" />
          </template>
        </van-cell>
        <!-- 启动/停止按钮 -->
        <div class="p-3">
          <van-button
            v-if="schedStatus?.status !== 'running'"
            type="success"
            block
            @click="handleStartSched"
          >
            启动调度
          </van-button>
          <van-button
            v-else
            type="danger"
            block
            @click="handleStopSched"
          >
            停止调度
          </van-button>
        </div>
      </van-cell-group>

      <!-- ============================ 调度状态展示 ============================ -->
      <van-cell-group v-if="schedStatus?.status === 'running'" inset>
        <van-cell title="状态" value="运行中" />
        <van-cell title="策略" :value="schedStatus.strategy || '-'" />
        <van-cell title="执行规则" :value="cronFriendly(schedStatus.cron || '')" />
        <van-cell v-if="schedStatus.next_run" title="下次执行" :value="schedStatus.next_run" />
      </van-cell-group>
      <van-empty v-else-if="schedStatus" description="调度器未启动" :image-size="60" />

    </div>

    <!-- ============================ 弹窗选择器 ============================ -->

    <!-- 手动选股策略选择 -->
    <van-popup v-model:show="showStrategyPicker" position="bottom" round>
      <van-picker
        :columns="[{ text: '短线', value: 'short' }, { text: '波段', value: 'swing' }]"
        @confirm="({ selectedValues }: any) => { runForm.strategy = selectedValues[0]; showStrategyPicker = false }"
        @cancel="showStrategyPicker = false"
      />
    </van-popup>

    <!-- 调度策略选择 -->
    <van-popup v-model:show="showSchedStrategyPicker" position="bottom" round>
      <van-picker
        :columns="[{ text: '短线', value: 'short' }, { text: '波段', value: 'swing' }]"
        @confirm="({ selectedValues }: any) => { schedForm.strategy = selectedValues[0]; showSchedStrategyPicker = false }"
        @cancel="showSchedStrategyPicker = false"
      />
    </van-popup>

    <!-- 日期选择 -->
    <van-popup v-model:show="showDatePicker" position="bottom" round>
      <van-date-picker
        v-model="datePickerValue"
        @confirm="onDateConfirm"
        @cancel="showDatePicker = false"
      />
    </van-popup>

    <!-- 时间选择 -->
    <van-popup v-model:show="showTimePicker" position="bottom" round>
      <van-picker
        :columns="timeColumns"
        @confirm="onTimeConfirm"
        @cancel="showTimePicker = false"
      />
    </van-popup>
  </div>
</template>
