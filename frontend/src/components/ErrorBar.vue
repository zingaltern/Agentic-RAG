<script setup>
import { useAppStore } from '../stores/app'

/** 全局错误码提示条（五区块之五）：api() 非 0 code 统一在此展示。 */
const store = useAppStore()
const errors = store.errors
</script>

<template>
  <div class="error-bar">
    <el-alert
      v-for="e in errors"
      :key="e.id"
      :title="e.text"
      type="error"
      :closable="false"
      show-icon
      class="error-bar-item"
    />
  </div>
</template>
