<template>
  <div class="min-h-screen bg-gray-50">
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4 flex items-center justify-between">
        <div class="flex items-center gap-4">
          <router-link to="/" class="text-gray-500 hover:text-gray-700">&larr; 返回</router-link>
          <h1 class="text-lg font-bold">任务执行</h1>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6 space-y-6">

    <!-- 手动选股 -->
    <div class="bg-white rounded-lg shadow p-6 space-y-4">
      <h2 class="text-lg font-semibold text-gray-700">手动选股</h2>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <label class="block text-sm text-gray-600 mb-1">策略</label>
          <select v-model="runForm.strategy" class="w-full border rounded px-2 py-1.5 text-sm">
            <option value="short">短线</option>
            <option value="swing">波段</option>
          </select>
        </div>
        <div>
          <label class="block text-sm text-gray-600 mb-1">日期（可选）</label>
          <input v-model="runForm.tradeDate" type="date" class="w-full border rounded px-2 py-1.5 text-sm" />
        </div>
        <div class="flex items-end">
          <label class="flex items-center gap-2 text-sm text-gray-600">
            <input v-model="runForm.skipAi" type="checkbox" class="rounded" />
            跳过 AI
          </label>
        </div>
        <div class="flex items-end">
          <button
            @click="handleRun"
            :disabled="taskStatus?.status === 'running'"
            class="w-full bg-blue-600 text-white rounded px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {{ taskStatus?.status === 'running' ? '执行中...' : '执行选股' }}
          </button>
        </div>
      </div>

      <!-- 任务状态 -->
      <div v-if="taskStatus && taskStatus.status !== 'idle'" class="border rounded p-3 text-sm space-y-1"
        :class="taskStatus.status === 'running' ? 'bg-blue-50 border-blue-200' : taskStatus.status === 'error' ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-200'">
        <div class="flex items-center gap-2">
          <span v-if="taskStatus.status === 'running'" class="animate-spin inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full"></span>
          <span class="font-medium">
            {{ taskStatus.status === 'running' ? '正在执行' : taskStatus.status === 'error' ? '执行失败' : '已完成' }}
          </span>
        </div>
        <div v-if="taskStatus.strategy" class="text-gray-600">策略：{{ taskStatus.strategy }} | 日期：{{ taskStatus.trade_date }}</div>
        <div v-if="taskStatus.started_at" class="text-gray-500 text-xs">开始时间：{{ taskStatus.started_at }}</div>
        <div v-if="taskStatus.error" class="text-red-600">{{ taskStatus.error }}</div>
      </div>
    </div>

    <!-- 定时调度 -->
    <div class="bg-white rounded-lg shadow p-6 space-y-4">
      <h2 class="text-lg font-semibold text-gray-700">定时调度</h2>

      <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div>
          <label class="block text-sm text-gray-600 mb-1">策略</label>
          <select v-model="schedForm.strategy" :disabled="schedStatus?.status === 'running'" class="w-full border rounded px-2 py-1.5 text-sm disabled:bg-gray-100">
            <option value="short">短线</option>
            <option value="swing">波段</option>
          </select>
        </div>
        <div>
          <label class="block text-sm text-gray-600 mb-1">小时</label>
          <input v-model.number="schedForm.cronHour" type="number" min="0" max="23" :disabled="schedStatus?.status === 'running'" class="w-full border rounded px-2 py-1.5 text-sm disabled:bg-gray-100" />
        </div>
        <div>
          <label class="block text-sm text-gray-600 mb-1">分钟</label>
          <input v-model.number="schedForm.cronMinute" type="number" min="0" max="59" :disabled="schedStatus?.status === 'running'" class="w-full border rounded px-2 py-1.5 text-sm disabled:bg-gray-100" />
        </div>
        <div class="flex items-end">
          <label class="flex items-center gap-2 text-sm text-gray-600">
            <input v-model="schedForm.skipAi" type="checkbox" :disabled="schedStatus?.status === 'running'" class="rounded disabled:bg-gray-100" />
            跳过 AI
          </label>
        </div>
        <div class="flex items-end gap-2">
          <button
            v-if="schedStatus?.status !== 'running'"
            @click="handleStartSched"
            class="flex-1 bg-green-600 text-white rounded px-3 py-1.5 text-sm hover:bg-green-700"
          >
            启动
          </button>
          <button
            v-else
            @click="handleStopSched"
            class="flex-1 bg-red-600 text-white rounded px-3 py-1.5 text-sm hover:bg-red-700"
          >
            停止
          </button>
        </div>
      </div>

      <!-- 调度状态 -->
      <div v-if="schedStatus?.status === 'running'" class="border rounded p-3 text-sm bg-green-50 border-green-200 space-y-1">
        <div class="flex items-center gap-2">
          <span class="inline-block w-2 h-2 bg-green-500 rounded-full"></span>
          <span class="font-medium text-green-700">调度运行中</span>
        </div>
        <div class="text-gray-600">策略：{{ schedStatus.strategy }} | Cron：{{ schedStatus.cron }}</div>
        <div v-if="schedStatus.next_run" class="text-gray-500 text-xs">下次执行：{{ schedStatus.next_run }}</div>
      </div>
      <div v-else class="text-sm text-gray-400">调度器未启动</div>
    </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, reactive } from 'vue'
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

let pollTimer: ReturnType<typeof setInterval> | null = null

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

onMounted(() => {
  pollStatus()
  pollTimer = setInterval(pollStatus, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
