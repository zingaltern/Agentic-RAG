<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ChatDotRound, ChatLineRound, Cpu, Delete, MagicStick, Promotion } from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const kbId = ref('default')
const debug = ref(false)
const { chat } = storeToRefs(store)   // 会话状态提升到 store：切换页面不丢失
const bodyRef = ref(null)
let composing = false      // 中文输入法组合期：Enter 用于确认候选，不发送

onMounted(loadKbs)
async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) {
    kbs.value = res.data.kbs || []
    if (!kbs.value.some((k) => k.kb_id === kbId.value)) kbId.value = 'default'
  }
}

async function ask() {
  if (chat.value.loading) return            // A2：请求进行中禁止并发提交（Enter 路径）
  const q = chat.value.query.trim()
  if (!q) { store.pushError('请输入问题'); return }
  // 会话记忆：携带最近 6 轮（不含当前问题），供后端理解指代
  const history = chat.value.messages.slice(-6).map((m) => ({
    role: m.role,
    content: m.role === 'user' ? m.text : (m.text || ''),
  }))
  chat.value.messages.push({ role: 'user', text: q, time: new Date() })
  chat.value.query = ''
  chat.value.loading = true
  try {
    const res = await api('/api/query', {
      method: 'POST',
      body: JSON.stringify({ query: q, kb_id: kbId.value, debug: debug.value, history }),
    })
    store.setTraceId(res.trace_id)
    // A4：api() 不再 throw（网络失败返回 code:50001），此处统一渲染错误气泡
    chat.value.messages.push({
      role: 'assistant',
      text: res.code !== 0
        ? (res.message || `请求失败（${res.code}）`)
        : ((res.data && res.data.answer) || ''),
      result: { ...(res.data || {}), code: res.code },
      time: new Date(),
    })
  } catch (e) {
    chat.value.messages.push({
      role: 'assistant',
      text: `请求失败：${e && e.message ? e.message : e}`,
      result: { code: -1, message: String(e && e.message || e) },
      time: new Date(),
    })
  } finally {
    chat.value.loading = false
  }
}

function onEnter(e) {
  if (e.isComposing || composing) return   // IME 组合期（确认候选词）不发送
  ask()
}

function clearChat() {
  chat.value.messages = []
}

// 回答按 [N] 引用切段渲染（模板插值，禁 innerHTML/v-html）
function answerParts(msg) {
  return (msg.text || '').split(/(\[\d+\])/g).filter(Boolean).map((part) => ({
    text: part,
    citation: /^\[\d+\]$/.test(part),
  }))
}

function badgeType(result) {
  return result.hallucination_check === 'passed' ? 'success' : 'warning'
}
function badgeText(result) {
  return result.hallucination_check === 'passed' ? '幻觉检测通过' : '幻觉检测未通过（疑似）'
}
function stepTitle(step) {
  if (step.rewritten !== undefined) return `iter ${step.iter} · 改写 → ${(step.rewritten || []).join(' / ')}`
  if (step.llm_ms !== undefined) return `iter ${step.iter} · LLM 耗时 ${step.llm_ms}ms`
  return `iter ${step.iter} · query=${step.query} · score=${step.evaluator_score} · reason=${step.reason}`
}

function fmtTime(d) {
  if (!d) return ''
  return new Date(d).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

watch(
  () => chat.value.messages.length,
  () => nextTick(() => { if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight }),
)
</script>

<template>
  <div class="chat">
    <div class="card chat-header">
      <h2 class="card-title" style="flex: 1; margin: 0">
        <span class="title-icon"><el-icon><ChatLineRound /></el-icon></span>问答控制台
      </h2>
      <el-select v-model="kbId" style="width: 240px">
        <el-option v-for="k in kbs" :key="k.kb_id" :label="`${k.name}（${k.kb_id}）`" :value="k.kb_id" />
      </el-select>
      <el-checkbox v-model="debug">展示反思过程</el-checkbox>
      <el-button @click="clearChat" :disabled="!chat.messages.length">
        <el-icon style="margin-right: 5px"><Delete /></el-icon>清空对话
      </el-button>
    </div>

    <div class="card chat-body" ref="bodyRef">
      <div v-if="!chat.messages.length" class="chat-empty">
        <el-icon class="chat-empty-icon"><ChatDotRound /></el-icon>
        <p>基于知识库的智能问答</p>
        <p class="muted">输入问题开始提问，回答将带引用来源与溯源评分</p>
      </div>

      <template v-for="(m, i) in chat.messages" :key="i">
        <div v-if="m.role === 'user'" class="msg-row msg-user">
          <div class="msg-main">
            <div class="bubble bubble-user">{{ m.text }}</div>
            <div class="msg-meta">{{ fmtTime(m.time) }}</div>
          </div>
          <div class="avatar avatar-user"><el-icon><Promotion /></el-icon></div>
        </div>

        <div v-else class="msg-row msg-ai">
          <div class="avatar avatar-ai"><el-icon><Cpu /></el-icon></div>
          <div class="msg-main">
            <div class="bubble bubble-ai">
              <template v-if="m.result && m.result.code !== 0">
                <div class="answer-box" style="margin: 0">
                  <span class="muted">[错误码 {{ m.result.code }}]</span> {{ m.text || '（无兜底文案）' }}
                </div>
              </template>
              <template v-else>
                <div class="answer-text">
                  <template v-for="(part, j) in answerParts(m)" :key="j">
                    <span v-if="part.citation" class="cite">{{ part.text }}</span>
                    <template v-else>{{ part.text }}</template>
                  </template>
                </div>
                <div v-if="m.result" class="meta-row">
                  <el-tag :type="badgeType(m.result)" size="small">{{ badgeText(m.result) }}</el-tag>
                  <span class="meta-text">引用 {{ (m.result.citations && m.result.citations.used) || [] }}
                    · 已证率 {{ ((m.result.citations && m.result.citations.confirmed) ?? 0).toFixed(2) }}
                    · 迭代 {{ m.result.iterations }}</span>
                </div>
                <el-collapse v-if="m.result && m.result.sources && m.result.sources.length" class="sources">
                  <el-collapse-item :title="`引用来源（${m.result.sources.length}）· 已证率 ${(((m.result.citations && m.result.citations.confirmed) ?? 0)).toFixed(2)}`" :name="1">
                    <el-table :data="m.result.sources" size="small" border>
                      <el-table-column prop="filename" label="文件" min-width="140" />
                      <el-table-column prop="page" label="页码" width="70" />
                      <el-table-column prop="score" label="相关度评分" width="100">
                        <template #default="{ row }">{{ row.score.toFixed(3) }}</template>
                      </el-table-column>
                      <el-table-column label="片段">
                        <template #default="{ row }">{{ (row.chunk || '').slice(0, 200) }}</template>
                      </el-table-column>
                    </el-table>
                  </el-collapse-item>
                </el-collapse>

                <el-collapse
                  v-if="debug && m.result && m.result.debug_trace && m.result.debug_trace.length"
                  class="sources"
                >
                  <el-collapse-item :title="`反思过程（${m.result.debug_trace.length} 步）`" :name="1">
                    <template v-for="(step, k) in m.result.debug_trace" :key="k">
                      <div class="debug-step-title">{{ stepTitle(step) }}</div>
                      <pre class="debug-step">{{ JSON.stringify(step, null, 2) }}</pre>
                    </template>
                  </el-collapse-item>
                </el-collapse>
              </template>
            </div>
            <div class="msg-time">{{ fmtTime(m.time) }}</div>
          </div>
        </div>
      </template>

      <div v-if="chat.loading" class="msg-row msg-ai">
        <div class="avatar avatar-ai"><el-icon><Cpu /></el-icon></div>
        <div class="bubble bubble-ai typing">
          <span class="dot" /><span class="dot" /><span class="dot" />
        </div>
      </div>
    </div>

    <div class="card chat-input">
      <el-input
        v-model="chat.query"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="输入问题，Enter 发送；中文输入法中 Enter 仅确认候选字"
        @keydown.enter.exact.prevent="onEnter"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />
      <el-button type="primary" :loading="chat.loading" @click="ask">
        <el-icon style="margin-right: 5px"><MagicStick /></el-icon>提问
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; height: calc(100vh - 96px); gap: 14px; }
.chat-header { display: flex; align-items: center; gap: 12px; margin: 0; flex-wrap: wrap; }
.chat-body {
  flex: 1;
  overflow-y: auto;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 20px 24px;
  background:
    radial-gradient(1000px 420px at 0% 0%, rgba(255, 154, 112, .08), transparent 60%),
    radial-gradient(900px 400px at 100% 100%, rgba(255, 201, 158, .08), transparent 60%),
    #fff;
}
.chat-empty { margin: auto; text-align: center; color: #9aa6b8; }
.chat-empty-icon { font-size: 46px; color: #c6d2e6; }
.chat-empty p { margin: 8px 0 0; font-size: 14px; color: #5b6b82; }

.msg-row { display: flex; gap: 10px; align-items: flex-start; animation: msg-in .3s ease; }
.msg-user { justify-content: flex-end; }
.msg-ai { justify-content: flex-start; }
@keyframes msg-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.avatar {
  width: 36px; height: 36px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 17px; flex: none; color: #fff;
  box-shadow: 0 2px 8px rgba(15, 23, 42, .15);
}
.avatar-user { background: linear-gradient(135deg, #ff9a70, #ff7a45); }
.avatar-ai {
  background: linear-gradient(135deg, #ffc99e, #ff9a70);
  box-shadow: 0 2px 8px rgba(255, 154, 112, .35);
}
.msg-main { display: flex; flex-direction: column; max-width: 78%; }
.msg-user .msg-main { align-items: flex-end; }
.msg-ai .msg-main { align-items: flex-start; }
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.75;
  word-break: break-word;
}
.bubble-user {
  background: linear-gradient(135deg, #ff9a70, #ff7a45);
  color: #fff;
  border-bottom-right-radius: 6px;
  box-shadow: 0 4px 14px rgba(255, 122, 69, .3);
}
.bubble-ai {
  background: #fff;
  border: 1px solid #f2e5d3;
  border-bottom-left-radius: 6px;
  box-shadow: 0 2px 10px rgba(150, 105, 60, .08);
  color: #3d352b;
}
.msg-meta, .msg-time { font-size: 11px; color: #a3aec0; margin-top: 4px; }
.meta-row { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.meta-text { font-size: 12px; color: #8a94a6; }
.sources { margin-top: 12px; }
.typing { display: flex; gap: 5px; padding: 15px 18px; }
.dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #8aa4d8; animation: blink 1.2s infinite;
}
.dot:nth-child(2) { animation-delay: .2s; }
.dot:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
.chat-input { display: flex; gap: 12px; align-items: flex-end; margin: 0; }
.chat-input .el-input { flex: 1; }
.debug-step-title { font-size: 12px; font-weight: 600; color: #ff7a45; margin: 10px 0 4px; }
.debug-step {
  font-size: 12px;
  background: #fafbfd;
  border: 1px solid #eef1f6;
  padding: 8px 10px;
  border-radius: 8px;
  white-space: pre-wrap;
  max-height: 220px;
  overflow-y: auto;
  margin: 0;
}
</style>