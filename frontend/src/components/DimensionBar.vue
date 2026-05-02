<template>
  <div class="space-y-1">
    <div v-for="(value, key) in scores" :key="key" class="flex items-center gap-2">
      <span class="w-32 text-xs text-gray-600 truncate" :title="dimLabel(key as string)">{{ dimLabel(key as string) }}</span>
      <div class="flex-1 bg-gray-200 rounded h-4">
        <div
          class="bg-blue-500 h-4 rounded"
          :style="{ width: `${value}%` }"
        />
      </div>
      <span class="w-8 text-right text-xs text-gray-700">{{ value }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { dimLabel } from '../api'

defineProps<{
  scores: Record<string, number>
}>()
</script>
