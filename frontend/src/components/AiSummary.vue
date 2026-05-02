<template>
  <van-notice-bar
    v-if="summary"
    :text="`AI：${summary}`"
    left-text="AI"
    color="#1989fa"
    background="#ecf9ff"
  />
  <div v-else class="text-xs text-gray-400 py-1">AI 分析缺失</div>
</template>

<script setup lang="ts">
defineProps<{ summary: string | null }>()
</script>
