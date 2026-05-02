<template>
  <div class="min-h-screen bg-gray-50">
    <van-nav-bar
      title="任务执行"
      left-text="返回"
      left-arrow
      @click-left="$router.push('/')"
    />

    <div class="px-3 py-4 space-y-4">
      <!-- 手动选股 -->
      <van-cell-group inset title="手动选股">
        <van-field
          :model-value="runForm.strategy === 'short' ? '短线' : '波段'"
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
          placeholder="默认今天"
          @click="showDatePicker = true"
        />
        <van-cell title="跳过 AI">
          <template #value>
            <van-switch v-model="runForm.skipAi" size="20px" />
          </template>
        </van-cell>
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

      <!-- 任务状态 -->
      <van-notice-bar
        v-if="taskStatus && taskStatus.status === 'running'"
        left-text="正在执行"
        :text="`${taskStatus.strategy === 'short' ? '短线' : '波段'} · ${taskStatus.trade_date} · ${taskStatus.started_at}`"
      />
      <van-notice-bar
        v-else-if="taskStatus && taskStatus.status === 'error'"
        color="#ee0a24"
        background="#ffe1e1"
        left-text="执行失败"
        :text="taskStatus.error || ''"
      />
      <van-notice-bar
        v-else-if="taskStatus && taskStatus.status === 'idle' && taskStatus.trade_date"
        left-text="执行完成"
        :text="`${taskStatus.strategy === 'short' ? '短线' : '波段'} · ${taskStatus.trade_date}`"
      />

      <!-- 定时调度 -->
      <van-cell-group inset title="定时调度">
        <van-field
          :model-value="schedForm.strategy === 'short' ? '短线' : '波段'"
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

      <!-- 调度状态 -->
      <van-cell-group v-if="schedStatus?.status === 'running'" inset>
        <van-cell title="状态" value="运行中" />
        <van-cell title="策略" :value="schedStatus.strategy || '-'" />
        <van-cell title="执行规则" :value="cronFriendly(schedStatus.cron || '')" />
        <van-cell v-if="schedStatus.next_run" title="下次执行" :value="schedStatus.next_run" />
      </van-cell-group>
      <van-empty v-else-if="schedStatus" description="调度器未启动" :image-size="60" />
    </div>

    <!-- Pickers -->
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

<script setup lang="ts">
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

const datePickerValue = ref(['2026', '05', '02'])

const timeColumns = computed(() => [
  Array.from({ length: 24 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
  Array.from({ length: 60 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i })),
])

function onDateConfirm({ selectedValues }: { selectedValues: string[] }) {
  runForm.tradeDate = selectedValues.join('-')
  showDatePicker.value = false
}

function onTimeConfirm({ selectedValues }: { selectedValues: number[] }) {
  schedForm.cronHour = selectedValues[0]
  schedForm.cronMinute = selectedValues[1]
  showTimePicker.value = false
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
    // ignore
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  pollStatus()
  pollTimer = setInterval(pollStatus, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
