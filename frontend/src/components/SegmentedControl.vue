<script setup>
/**
 * Apple 式分段控件：单一选中态、滑动指示块、支持图标与紧凑尺寸。
 * 相比多个 checkbox/switch，语义更清晰（互斥选项）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
  options: { type: Array, required: true },   // [{ value, label, icon? }]
  size: { type: String, default: 'md' },      // md | sm
})
const emit = defineEmits(['update:modelValue', 'change'])

const rootRef = ref(null)
const itemEls = []
const thumbStyle = ref({})
let observer = null

const activeIndex = computed(() => {
  const i = props.options.findIndex((o) => o.value === props.modelValue)
  return i < 0 ? 0 : i
})

function setItemRef(el, index) {
  itemEls[index] = el
}

/** 指示块按「实测宽度 + offsetLeft」定位：中英文混排、字数不同的选项也不会错位。 */
function measure() {
  const el = itemEls[activeIndex.value]
  if (!el) return
  thumbStyle.value = {
    width: `${el.offsetWidth}px`,
    // thumb 的 left 是 3px（容器 padding），offsetLeft 含该 padding，故减 3
    transform: `translateX(${Math.max(el.offsetLeft - 3, 0)}px)`,
  }
}

function select(option) {
  if (option.value === props.modelValue) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
}

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined' && rootRef.value) {
    observer = new ResizeObserver(() => measure())
    observer.observe(rootRef.value)
  }
})
onUnmounted(() => { if (observer) observer.disconnect() })

watch(activeIndex, () => nextTick(measure))
watch(() => props.options.length, () => nextTick(measure))
</script>

<template>
  <div ref="rootRef" class="seg" :class="`seg--${size}`" role="tablist">
    <span class="seg-thumb" :style="thumbStyle" />
    <button
      v-for="(option, index) in options"
      :key="String(option.value)"
      :ref="(el) => setItemRef(el, index)"
      type="button"
      role="tab"
      class="seg-item"
      :class="{ 'is-active': option.value === modelValue }"
      :aria-selected="option.value === modelValue"
      @click="select(option)"
    >
      <el-icon v-if="option.icon" class="seg-icon"><component :is="option.icon" /></el-icon>
      <span>{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.seg {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: var(--fill-1);
  border: 1px solid var(--hairline);
  user-select: none;
}
.seg-thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 3px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12), 0 1px 1px rgba(0, 0, 0, 0.06);
  transition: transform 260ms var(--ease), width 260ms var(--ease);
}
html[data-theme="dark"] .seg-thumb { background: var(--surface-raised); }
.seg-item {
  position: relative;
  z-index: 1;
  flex: 1 1 0;
  justify-content: center;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 16px;
  border: none;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: color 200ms var(--ease);
}
.seg-item:hover { color: var(--text); }
.seg-item.is-active { color: var(--text); font-weight: 600; }
.seg--sm .seg-item { height: 24px; padding: 0 11px; font-size: 12px; }
.seg-icon { font-size: 13px; }
</style>
