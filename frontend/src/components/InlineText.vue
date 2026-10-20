<script setup>
/**
 * 行内富文本渲染：**粗体** / `代码` / [n] 引用标号。
 * 只输出结构化 token + 模板插值（天然转义），不使用 v-html。
 */
import { computed } from 'vue'
import { parseInline } from '../utils/markdown'

const props = defineProps({
  text: { type: String, default: '' },
  clickableCite: { type: Boolean, default: true },
})
const emit = defineEmits(['cite'])

const parts = computed(() => parseInline(props.text))
</script>

<template>
  <template v-for="(part, i) in parts" :key="i">
    <code v-if="part.type === 'code'" class="inline-code">{{ part.text }}</code>
    <strong v-else-if="part.type === 'strong'" class="inline-strong">{{ part.text }}</strong>
    <button
      v-else-if="part.type === 'cite' && clickableCite"
      type="button"
      class="inline-cite"
      :title="`查看引用来源 ${part.text}`"
      @click="emit('cite', part.text)"
    >{{ part.text }}</button>
    <span v-else-if="part.type === 'cite'" class="inline-cite inline-cite--static">{{ part.text }}</span>
    <template v-else>{{ part.text }}</template>
  </template>
</template>

<style scoped>
.inline-code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--fill-1);
  border: 1px solid var(--hairline);
  font-family: var(--font-mono);
  font-size: 0.9em;
}
.inline-strong { font-weight: 650; }
.inline-cite {
  display: inline-flex;
  align-items: center;
  height: 18px;
  margin: 0 1px;
  padding: 0 5px;
  border: none;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  line-height: 1;
  vertical-align: 1px;
  cursor: pointer;
  transition: background var(--dur) var(--ease);
}
.inline-cite:hover { background: var(--accent-soft-2); }
.inline-cite--static { cursor: default; }
</style>
