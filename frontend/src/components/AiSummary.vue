<template>
  <div v-if="summary" class="mt-2 text-sm text-gray-600">
    <span class="font-medium text-gray-800">AI：</span>{{ summary }}
  </div>
  <div v-else class="mt-2 text-sm text-gray-400">AI 分析缺失</div>
</template>

<script setup lang="ts">
defineProps<{ summary: string | null }>()
</script>
