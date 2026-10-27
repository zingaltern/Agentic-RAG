<script setup>
/**
 * 回答元信息条：运行模式 / 终止原因 / 已证率 / 引用 / 耗时 / 步数。
 *
 * - showVerification=false 时只展示中性的模式/耗时/步数（兜底、澄清等错误分支）；
 * - Agent 用 agent_steps 显示“Agent N 步”，只有 reflect 才显示“反思 N 轮”；
 * - 只有带证据的正常回答才应传 showVerification=true，避免问候被标“待核实”。
 */
import { computed } from 'vue'
import { CircleCheck, CircleClose } from '@element-plus/icons-vue'
import { formatDuration, formatPercent } from '../utils/format'
import { modeLabel, stepLabel, terminationLabel } from '../utils/answerView'

const props = defineProps({
  result: { type: Object, default: null },
  showVerification: { type: Boolean, default: true },
})

const rate = computed(() => {
  const n = Number(props.result && props.result.citations && props.result.citations.confirmed)
  return Math.min(Math.max(Number.isFinite(n) ? n : 0, 0), 1)
})

const passed = computed(() => {
  const check = props.result && props.result.hallucination_check
  if (check === 'passed') return true
  if (check === 'suspected') return false
  return rate.value >= 0.6
})

const cited = computed(() => {
  const used = props.result && props.result.citations && props.result.citations.used
  const list = Array.isArray(used) ? used : (used ? [used] : [])
  const sourceCount = Array.isArray(props.result && props.result.sources)
    ? props.result.sources.length : 0
  // 越界编号（如 [999]）不得冒充有效引用；后端已过滤，前端再兜底一次。
  const valid = list.filter((item) => {
    const n = Number(String(item).replace(/[^\d]/g, ''))
    return Number.isFinite(n) && n >= 1 && n <= sourceCount
  })
  return valid.length ? valid.join(' ') : ''
})

const elapsed = computed(() => {
  const n = Number(props.result && props.result.elapsed_ms)
  return Number.isFinite(n) && n > 0 ? formatDuration(n) : ''
})

const mode = computed(() => modeLabel(props.result))
const termination = computed(() => terminationLabel(props.result))
const steps = computed(() => stepLabel(props.result))
</script>

<template>
  <div v-if="result" class="answer-meta">
    <span v-if="mode" class="meta-chip meta-chip--mode">{{ mode }}</span>
    <span v-if="termination" class="meta-chip meta-chip--warn">{{ termination }}</span>
    <el-tag v-if="showVerification" :type="passed ? 'success' : 'warning'" size="small" effect="light">
      <el-icon class="meta-icon">
        <CircleCheck v-if="passed" />
        <CircleClose v-else />
      </el-icon>
      {{ passed ? `已证 ${formatPercent(rate)} · 通过` : `已证 ${formatPercent(rate)} · 待核实` }}
    </el-tag>
    <span v-if="cited" class="meta-chip mono">引用 {{ cited }}</span>
    <span v-if="elapsed" class="meta-chip">{{ elapsed }}</span>
    <span v-if="steps" class="meta-chip">{{ steps }}</span>
    <span v-if="result.index_dna_mismatch" class="meta-chip meta-chip--warn">索引配置不一致</span>
  </div>
</template>

<style scoped>
.answer-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}
.meta-icon { margin-right: 4px; vertical-align: -2px; }
.meta-chip {
  font-size: 11.5px;
  color: var(--text-tertiary);
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--fill-1);
}
.meta-chip--warn { color: var(--warning); background: var(--warning-soft); }
.meta-chip--mode { color: var(--accent); background: var(--accent-soft); font-weight: 600; }
</style>
