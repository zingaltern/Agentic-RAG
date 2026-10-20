<script setup>
/** 页面大标题（Apple 式 Large Title + 副标题 + 右侧操作区）。 */
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
})
</script>

<template>
  <header class="page-head">
    <div class="head-text">
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h1 class="head-title">{{ title }}</h1>
      <p v-if="subtitle" class="head-sub">{{ subtitle }}</p>
    </div>
    <div class="head-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  padding: 2px 2px 4px;
}
.head-text { min-width: 0; flex: 1; }
.eyebrow {
  margin: 0 0 4px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-tertiary);
}
.head-title {
  font-size: 27px;
  line-height: 1.15;
  letter-spacing: -0.026em;
  font-weight: 700;
}
.head-sub {
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .head-title { font-size: 22px; }
  .head-actions { width: 100%; }
}
</style>
