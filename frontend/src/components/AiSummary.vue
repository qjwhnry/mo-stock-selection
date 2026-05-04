<script setup lang="ts">
/**
 * AI 摘要提示条组件
 *
 * 功能：在股票卡片展开时显示 AI 简要总结
 * - 有 summary 时以蓝色 NoticeBar 展示
 * - 无 summary 时显示"AI 分析缺失"占位文字
 */

defineProps<{ summary: string | null }>()
</script>

<template>
  <!-- 有 AI 摘要时以通知条展示 -->
  <van-notice-bar
    v-if="summary"
    :text="`AI：${summary}`"
    left-text="AI"
    color="#1989fa"
    background="#ecf9ff"
  />
  <!-- 无摘要时显示占位 -->
  <div v-else class="text-xs text-gray-400 py-1">AI 分析缺失</div>
</template>