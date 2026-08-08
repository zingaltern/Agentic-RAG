<script setup>
import { onMounted, ref, computed } from 'vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const kbId = ref('default')
const query = ref('')
const debug = ref(true)
const loading = ref(false)

const result = ref(null)       // {answer, sources, citations, hallucination_check, iterations, elapsed_ms, debug_trace}
const traceId = ref('')

onMounted(loadKbs)
async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) {
    kbs.value = res.data.kbs || []
    if (!kbs.value.some((k) => k.kb_id === kbId.value)) kbId.value = 'default'
  }
}

async function ask() {
  if (!query.value.trim()) { store.pushError('请输入问题'); return }
  loading.value = true
  result.value = null
  try {
    const res = await api('/api/query', {
      method: 'POST',
      body: JSON.stringify({ query: query.value, kb_id: kbId.value, debug: debug.value }),
    })
    traceId.value = res.trace_id || ''
    store.setTraceId(res.trace_id)
    result.value = { ...(res.data || {}), code: res.code }
  } finally {
    loading.value = false
  }
}

// S3：answer 按 [N] 引用切段渲染（模板插值，禁 innerHTML/v-html）
const answerParts = computed(() => {
  const text = (result.value && result.value.answer) || ''
  return text.split(/(\[\d+\])/g).filter(Boolean).map((part) => ({
    text: part,
    citation: /^\[\d+\]$/.test(part),
  }))
})

const badgeType = computed(() => {
  const h = result.value && result.value.hallucination_check
  return h === 'passed' ? 'success' : 'warning'
})
const badgeText = computed(() => {
  const h = result.value && result.value.hallucination_check
  return h === 'passed' ? '幻觉检测通过 (passed)' : '幻觉检测未通过 (suspected)'
})

function stepTitle(step) {
  if (step.rewritten !== undefined) return `iter ${step.iter} · 改写 → ${(step.rewritten || []).join(' / ')}`
  if (step.llm_ms !== undefined) return `iter ${step.iter} · LLM 耗时 ${step.llm_ms}ms`
  return `iter ${step.iter} · query=${step.query} · score=${step.evaluator_score} · reason=${step.reason}`
}
function stepText(step) {
  return JSON.stringify(step, null, 2)
}
</script>

<template>
  <div>
    <div class="card">
      <h2>① 问答控制台</h2>
      <div class="row">
        <span>知识库：</span>
        <el-select v-model="kbId" style="width: 220px">
          <el-option v-for="k in kbs" :key="k.kb_id" :label="`${k.name}（${k.kb_id}）`" :value="k.kb_id" />
        </el-select>
        <el-checkbox v-model="debug">debug（反思过程）</el-checkbox>
        <el-button type="primary" :loading="loading" @click="ask">提问</el-button>
      </div>
      <el-input v-model="query" type="textarea" :rows="3" placeholder="输入问题，如：年假怎么申请？" />
    </div>

    <div v-if="result" class="card">
      <h2>回答</h2>
      <div v-if="result.code !== 0" class="answer-box">
        <span class="muted">[错误码 {{ result.code }}]</span> {{ result.answer || '（无兜底文案）' }}
      </div>
      <div v-else>
        <div class="answer-box">
          <div class="answer-text">
            <template v-for="(part, i) in answerParts" :key="i">
              <span v-if="part.citation" class="cite">{{ part.text }}</span>
              <template v-else>{{ part.text }}</template>
            </template>
          </div>
        </div>
        <div class="row">
          <el-tag :type="badgeType" size="small">{{ badgeText }}</el-tag>
          <span class="muted">引用 {{ (result.citations && result.citations.used) || [] }} ·
            已证率 {{ ((result.citations && result.citations.confirmed) ?? 0).toFixed(2) }} ·
            迭代 {{ result.iterations }} · 耗时 {{ result.elapsed_ms }}ms</span>
        </div>
        <div class="trace-line" v-if="traceId">trace_id: {{ traceId }}（服务端 JSON 审计日志同号）</div>

        <h2 style="margin-top: 14px">来源</h2>
        <el-table :data="result.sources || []" size="small" border>
          <el-table-column prop="filename" label="文件" min-width="140" />
          <el-table-column prop="page" label="页码" width="70" />
          <el-table-column prop="score" label="分数" width="80">
            <template #default="{ row }">{{ row.score.toFixed(3) }}</template>
          </el-table-column>
          <el-table-column label="片段">
            <template #default="{ row }">{{ (row.chunk || '').slice(0, 200) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div v-if="debug && result && result.debug_trace && result.debug_trace.length" class="card">
      <h2>④ 反思过程（debug_trace）</h2>
      <el-collapse>
        <el-collapse-item
          v-for="(step, i) in result.debug_trace"
          :key="i"
          :title="stepTitle(step)"
          :name="i"
        >
          <pre class="debug-step">{{ stepText(step) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<style scoped>
.debug-step { font-size: 12px; background: #fafafa; padding: 8px; border-radius: 6px; white-space: pre-wrap; }
</style>
