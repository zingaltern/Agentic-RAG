<script setup>
/**
 * 反思过程 / Agent 计划折叠面板。
 *
 * 过滤 debug_trace 中的 fast_path（如 greeting），快捷通道不再显示成“反思 1 步”。
 * 正常回答与兜底/澄清错误分支共用，保证失败反思也可见（D04）。
 */
import { computed } from 'vue'
import { goalEvidenceText, reflectionSteps, tracePanelTitle, traceStepLabel } from '../utils/answerView'

const props = defineProps({
  result: { type: Object, default: null },
  debug: { type: Boolean, default: false },
})

const steps = computed(() => reflectionSteps(props.result))
const plan = computed(() => props.result && props.result.plan)
const agentSteps = computed(() => (props.result && props.result.steps) || [])
const agentTrace = computed(() => {
  const trace = props.result && props.result.agent_trace
  return Array.isArray(trace) ? trace : []
})
</script>

<template>
  <details v-if="debug && result && steps.length" class="trace">
    <summary>{{ tracePanelTitle(result, steps.length) }}</summary>
    <div v-for="(step, k) in steps" :key="k" class="trace-step">
      <p class="trace-title">
        {{ traceStepLabel(step, k) }}<span v-if="step.reason"> · {{ step.reason }}</span><span v-if="step.evaluator_score !== undefined"> · 评分 {{ step.evaluator_score }}</span>
      </p>
      <p v-if="step.query" class="trace-query mono">query: {{ step.query }}</p>
      <p v-if="step.rewritten && step.rewritten.length" class="trace-query mono">rewrite: {{ step.rewritten.join(' / ') }}</p>
      <pre class="trace-json">{{ JSON.stringify(step, null, 2) }}</pre>
    </div>
  </details>

  <details v-if="debug && result && plan" class="trace">
    <summary>Agent 计划（{{ (plan.subgoals || []).length }} 个子目标）</summary>
    <div v-for="g in (plan.subgoals || [])" :key="g.goal_id" class="trace-step">
      <p class="trace-title">{{ g.goal_id }} · {{ g.question }}<span v-if="g.status"> · {{ g.status }}</span></p>
      <p v-if="g.success_criteria" class="trace-query">判据：{{ g.success_criteria }}</p>
      <p v-if="goalEvidenceText(g)" class="trace-query mono">{{ goalEvidenceText(g) }}</p>
    </div>
    <div v-for="(st, k) in agentSteps" :key="`st-${k}`" class="trace-step">
      <p class="trace-title">第 {{ st.step ?? k + 1 }} 步 · {{ st.action }}</p>
      <p v-if="st.summary" class="trace-query">{{ st.summary }}</p>
      <p v-if="st.evidence_ids && st.evidence_ids.length" class="trace-query mono">证据：{{ st.evidence_ids.join(' / ') }}</p>
    </div>
  </details>

  <details v-if="debug && agentTrace.length" class="trace">
    <summary>Agent 执行轨迹（{{ agentTrace.length }} spans）</summary>
    <div v-for="(span, k) in agentTrace" :key="span.span_id || k" class="trace-step">
      <p class="trace-title">
        {{ span.span_id }} · {{ span.type }}<span v-if="span.name"> · {{ span.name }}</span><span v-if="span.step !== undefined"> · step {{ span.step }}</span><span v-if="typeof span.duration_ms === 'number'"> · {{ span.duration_ms }} ms</span>
      </p>
      <p v-if="span.input_summary" class="trace-query mono">in: {{ span.input_summary }}</p>
      <p v-if="span.output_summary" class="trace-query">out: {{ span.output_summary }}</p>
      <pre v-if="span.attributes && Object.keys(span.attributes).length" class="trace-json">{{ JSON.stringify(span.attributes, null, 2) }}</pre>
    </div>
  </details>
</template>

<style scoped>
.trace { margin-top: 10px; }
.trace summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  padding: 6px 0;
  list-style: none;
}
.trace summary::-webkit-details-marker { display: none; }
.trace summary::before { content: '▸ '; color: var(--text-tertiary); }
.trace[open] summary::before { content: '▾ '; }
.trace-step { padding: 8px 0; border-top: 1px solid var(--hairline); }
.trace-title { margin: 0 0 4px; font-size: 12px; font-weight: 600; color: var(--text-secondary); }
.trace-query { margin: 0; font-size: 11.5px; color: var(--text-tertiary); word-break: break-all; }
.trace-json {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 200px;
  overflow: auto;
  border-radius: var(--radius-xs);
  background: var(--bg-deep);
  border: 1px solid var(--hairline);
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-secondary);
}
</style>
