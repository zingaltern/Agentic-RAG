<script setup>
/** 空态：图标 + 标题 + 说明 + 操作插槽（列表/问答/抽屉复用）。 */
defineProps({
  icon: { type: [Object, Function], default: null },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <div class="empty" :class="{ 'empty--compact': compact }">
    <div v-if="icon" class="empty-icon">
      <el-icon><component :is="icon" /></el-icon>
    </div>
    <p v-if="title" class="empty-title">{{ title }}</p>
    <p v-if="description" class="empty-desc">{{ description }}</p>
    <div v-if="$slots.default" class="empty-actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 44px 24px;
  gap: 4px;
  animation: fade-up 320ms var(--ease) both;
}
.empty--compact { padding: 22px 12px; }
.empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 54px;
  height: 54px;
  margin-bottom: 10px;
  border-radius: 18px;
  font-size: 24px;
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-soft-2);
}
.empty-title { margin: 0; font-size: 15px; font-weight: 600; color: var(--text); }
.empty-desc {
  margin: 4px 0 0;
  max-width: 420px;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-tertiary);
}
.empty-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 14px; }
</style>
