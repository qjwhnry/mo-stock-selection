<script setup lang="ts">
/**
 * 首页 / 报告列表页
 *
 * 功能：
 * 1. 按策略（短线/波段）切换展示历史选股报告列表
 * 2. 每条报告卡片显示日期、入选数量、平均分、最高分
 * 3. 点击卡片跳转至报告详情页 /report/:date
 * 4. 支持退出登录
 */

import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchReports, type ReportListItem } from '../api'
import { clearAuthSession } from '../auth'

const router = useRouter()

// 当前选中的策略标签（'short' 短线 / 'swing' 波段）
const strategy = ref('short')

// 分页相关状态
const page = ref(1)
const pageSize = 20
const total = ref(0)
const reports = ref<ReportListItem[]>([])
const loading = ref(false)
const error = ref('')

// 计算总页数（最少显示 1 页）
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

/**
 * 切换策略标签时重置页码并重新加载报告列表
 */
function onStrategyChange() {
  page.value = 1
  loadReports()
}

/**
 * 退出登录：清除本地认证信息并跳转到登录页
 */
function handleLogout() {
  clearAuthSession()
  router.push('/login')
}

/**
 * 从后端 API 获取报告列表数据
 */
async function loadReports() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await fetchReports(strategy.value, page.value, pageSize)
    reports.value = data.items
    total.value = data.total
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

// 组件挂载时自动加载报告列表
onMounted(loadReports)
</script>

<template>
  <!-- 页面容器：渐变背景色 -->
  <div class="home-page min-h-screen">

    <!-- 顶部导航栏 -->
    <van-nav-bar title="mo-stock 选股系统">
      <template #right>
        <!-- 右侧操作按钮：退出 + 跳转执行页 -->
        <div class="flex items-center gap-3">
          <button type="button" class="text-sm text-blue-600" @click="handleLogout">退出</button>
          <router-link to="/data" class="text-sm text-blue-600">数据洞察</router-link>
          <router-link to="/execute" class="text-sm text-blue-600">执行</router-link>
        </div>
      </template>
    </van-nav-bar>

    <!-- 策略切换标签页（短线 / 波段） -->
    <van-tabs v-model:active="strategy" @change="onStrategyChange">
      <van-tab title="短线" name="short" />
      <van-tab title="波段" name="swing" />
    </van-tabs>

    <div class="px-3 py-4">

      <!-- 加载中状态 -->
      <div v-if="loading" class="py-12 text-center text-gray-500">
        <van-loading size="24px">加载中...</van-loading>
      </div>

      <!-- 错误提示 -->
      <van-empty v-else-if="error" :description="error" />

      <!-- 无数据提示 -->
      <van-empty v-else-if="reports.length === 0" description="暂无选股数据，请先运行选股" />

      <!-- 报告卡片列表 -->
      <van-cell-group v-else inset class="report-list-card">
        <van-cell
          v-for="r in reports"
          :key="r.trade_date"
          :title="r.trade_date"
          :label="`入选 ${r.count} 只 · 平均 ${r.avg_score.toFixed(1)} 分 · 最高 ${r.max_score.toFixed(1)}`"
          is-link
          :to="`/report/${r.trade_date}?strategy=${strategy}`"
        />
      </van-cell-group>

      <!-- 分页控件（总数超过一页时显示） -->
      <div v-if="totalPages > 1" class="mt-4">
        <van-pagination
          v-model="page"
          :total-items="total"
          :items-per-page="pageSize"
          @change="loadReports"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页面背景渐变色（墨绿系） */
.home-page {
  min-height: 100vh;
  background:
    linear-gradient(180deg, #f8faf8 0%, #f2f5f3 46%, #eef2ef 100%);
  color: #1f2a25;
}

:deep(.van-nav-bar) {
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 1px 0 rgba(31, 42, 37, 0.06);
}

:deep(.van-nav-bar__title) {
  color: #1f2a25;
  font-weight: 700;
}

:deep(.van-tabs__wrap) {
  background: rgba(255, 255, 255, 0.82);
  box-shadow: inset 0 -1px 0 rgba(31, 42, 37, 0.05);
}

:deep(.van-tabs__nav) {
  background: transparent;
}

:deep(.van-tab) {
  color: #52615b;
}

:deep(.van-tab--active) {
  color: #17201d;
  font-weight: 700;
}

:deep(.van-tabs__line) {
  background: #1f8a7f;
}

:deep(.report-list-card) {
  overflow: hidden;
  border: 1px solid rgba(31, 42, 37, 0.06);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 12px 30px rgba(31, 42, 37, 0.04);
}

:deep(.report-list-card .van-cell) {
  background: transparent;
}

:deep(.report-list-card .van-cell__title) {
  color: #24302b;
}

:deep(.report-list-card .van-cell__label) {
  color: #7a8781;
}
</style>
