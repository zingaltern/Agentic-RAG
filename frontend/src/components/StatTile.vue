<script setup>
/** 指标卡：大号数字 + 标签 + 备注（概览区/空态引导复用）。 */
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  hint: { type: String, default: '' },
  icon: { type: [Object, Function], default: null },
  tone: { type: String, default: 'default' },   // default | accent
})
</script>

<template>
  <div class="stat" :class="`stat--${tone}`">
    <div class="stat-top">
      <span class="stat-label">{{ label }}</span>
      <el-icon v-if="icon" class="stat-icon"><component :is="icon" /></el-icon>
    </div>
    <div class="stat-value">{{ value }}</div>
    <div v-if="hint" class="stat-hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  background: var(--surface);
  border: 1px solid var(--hairline);
  min-width: 0;
}
.stat--accent {
  background: var(--accent-soft);
  border-color: var(--accent-soft-2);
}
.stat-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.stat-label { font-size: 12px; color: var(--text-tertiary); font-weight: 500; }
.stat-icon { color: var(--text-quaternary); font-size: 14px; }
.stat--accent .stat-icon { color: var(--accent); }
.stat-value {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.stat-hint { font-size: 11.5px; color: var(--text-tertiary); }
</style>
