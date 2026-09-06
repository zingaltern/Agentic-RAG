<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ChatDotRound, ChatLineRound, Cpu, Delete, MagicStick, Promotion } from '@element-plus/icons-vue'
import { api } from '../api'
import { friendlyError } from '../api/index.js'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const kbId = ref('default')
const debug = ref(false)
const emptyKb = ref(false)          // 当前知识库无文档 → 空态给引导（先上传再提问）
let msgSeq = 0                       // 消息稳定 key：避免 :key="i" 索引复用导致 DOM 状态错乱
const mkMsg = (msg) => ({ id: ++msgSeq, ...msg })
const { chat, webSearch } = storeToRefs(store)   // 会话/联网开关提升到 store：切页不丢
const bodyRef = ref(null)
const inputRef = ref(null)   // 编辑消息时聚焦输入框
let composing = false      // 中文输入法组合期：Enter 用于确认候选，不发送

onMounted(loadKbs)
async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) {
    kbs.value = res.data.kbs || []
    const prev = kbId.value
    if (!kbs.value.some((k) => k.kb_id === kbId.value)) kbId.value = 'default'
    // 仅当 kbId 未变化（watch 未触发）时手动刷新空库状态，避免双请求
    if (kbId.value === prev) await refreshKbDocs()
  }
}
async function refreshKbDocs() {
  const res = await api(`/api/documents?page=1&size=1&kb_id=${kbId.value}`)
  emptyKb.value = res.code === 0 && (res.data.total || 0) === 0
}
watch(kbId, refreshKbDocs)

async function ask() {
  if (chat.value.loading) return            // A2：请求进行中禁止并发提交（Enter 路径）
  const q = chat.value.query.trim()
  if (!q) { store.pushError('请输入问题'); return }
  // 会话记忆：携带最近 6 轮（不含当前问题）；仅用户提问与成功回答进历史，
  // 错误气泡（result.code !== 0）不进历史，避免污染模型上下文
  const history = chat.value.messages
    .slice(-6)
    .filter((m) => m.role === 'user' || (m.result && m.result.code === 0))
    .map((m) => ({
      role: m.role,
      content: m.role === 'user' ? m.text : (m.text || ''),
    }))
  // 13.13 T3：携带服务端会话 ID（dialogue 启用时后端历史优先；关闭时 history 兼容现状）
  const sessionId = store.ensureSessionId()
  chat.value.messages.push(mkMsg({ role: 'user', text: q, time: new Date() }))
  chat.value.query = ''
  chat.value.loading = true
  try {
    const res = await api('/api/query', {
      method: 'POST',
      body: JSON.stringify({ query: q, kb_id: kbId.value, debug: debug.value, web_search: webSearch.value, history, session_id: sessionId }),
    })
    // A4：api() 不再 throw（网络失败返回 code:50001），此处统一渲染错误气泡
    chat.value.messages.push(mkMsg({
      role: 'assistant',
      text: res.code !== 0
        ? friendlyError(res.code, res.message)
        : ((res.data && res.data.answer) || ''),
      result: { ...(res.data || {}), code: res.code },
      time: new Date(),
    }))
  } catch (e) {
    chat.value.messages.push(mkMsg({
      role: 'assistant',
      text: friendlyError(-1, String(e && e.message || e)),
      result: { code: -1, message: String(e && e.message || e) },
      time: new Date(),
    }))
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
  store.resetSession()   // 13.13 T3：清空对话 = 开启新会话（服务端历史/澄清计数随之重置）
}

// 回答按 [N] 引用切段渲染（模板插值，禁 innerHTML/v-html）
function answerParts(msg) {
  return (msg.text || '').split(/(\[\d+\])/g).filter(Boolean).map((part) => ({
    text: part,
    citation: /^\[\d+\]$/.test(part),
  }))
}

// 已证率较高（≥0.6）即视为通过，避免「高已证率却仍显示未通过」的误导
function getVerifiedRate(result) {
  return (result && result.citations && result.citations.confirmed) ?? 0
}
function passedCheck(result) {
  if (!result) return false
  if (result.hallucination_check === 'passed') return true
  return getVerifiedRate(result) >= 0.6
}
function badgeType(result) {
  return passedCheck(result) ? 'success' : 'warning'
}
function badgeText(result) {
  return passedCheck(result) ? '幻觉检测通过' : '幻觉检测未通过（疑似）'
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

// 消息气泡悬停操作：复制 / 编辑（编辑仅用户消息：回填输入框以便修改后重发）
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    store.pushInfo('已复制到剪贴板')
  } catch {
    store.pushError('复制失败，请手动选择复制')
  }
}
function editUserMessage(m) {
  chat.value.query = m.text || ''
  nextTick(() => { if (inputRef.value) inputRef.value.focus() })
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
        <el-option v-for="k in kbs" :key="k.kb_id" :label="k.name" :value="k.kb_id" />
      </el-select>
      <el-checkbox v-model="debug">展示反思过程</el-checkbox>
      <el-checkbox v-model="webSearch">联网搜索</el-checkbox>
      <el-button @click="clearChat" :disabled="!chat.messages.length">
        <el-icon style="margin-right: 5px"><Delete /></el-icon>清空对话
      </el-button>
    </div>

    <div class="card chat-body" ref="bodyRef">
      <div v-if="!chat.messages.length" class="chat-empty">
        <el-icon class="chat-empty-icon"><ChatDotRound /></el-icon>
        <p>基于知识库的智能问答</p>
        <p v-if="emptyKb" class="muted">
          当前知识库暂无文档——请先到
          <router-link to="/documents" class="empty-link">文档管理</router-link>上传文档后再提问
        </p>
        <p v-else class="muted">输入问题开始提问，回答将带引用来源与溯源评分</p>
      </div>

      <template v-for="(m) in chat.messages" :key="m.id">
        <div v-if="m.role === 'user'" class="msg-row msg-user">
          <div class="msg-main">
            <div class="bubble bubble-user">{{ m.text }}</div>
            <div class="msg-meta">{{ fmtTime(m.time) }}</div>
            <div class="msg-actions">
              <el-button text size="small" class="act-btn" @click="copyText(m.text)">复制</el-button>
              <el-button text size="small" class="act-btn" @click="editUserMessage(m)">编辑</el-button>
            </div>
          </div>
          <div class="avatar avatar-user"><el-icon><Promotion /></el-icon></div>
        </div>

        <div v-else class="msg-row msg-ai">
          <div class="avatar avatar-ai"><el-icon><Cpu /></el-icon></div>
          <div class="msg-main">
            <div class="bubble bubble-ai">
              <template v-if="m.result && m.result.code !== 0">
                <el-alert v-if="m.result.need_clarify" type="warning" :closable="false" show-icon
                          :title="'需要澄清：' + m.text" style="margin: 0" />
                <div v-else class="answer-box" style="margin: 0">{{ m.text }}</div>
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
                  <span class="meta-text">引用 {{ (m.result.citations && m.result.citations.used) || [] }}</span>
                </div>
                <el-collapse v-if="m.result && m.result.sources && m.result.sources.length" class="sources">
                  <el-collapse-item :title="`引用来源（${m.result.sources.length}）· 已证率 ${(((m.result.citations && m.result.citations.confirmed) ?? 0)).toFixed(2)}`" :name="1">
                    <el-table :data="m.result.sources" size="small" border>
                      <el-table-column label="来源" width="84">
                        <template #default="{ row }">
                          <el-tag v-if="row.source_type === 'web'" type="info" size="small" effect="plain">网页</el-tag>
                          <el-tag v-else type="success" size="small" effect="plain">知识库</el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column label="文件" min-width="140">
                        <template #default="{ row }">
                          <span class="src-file">{{ row.filename }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column label="页码" min-width="180">
                        <template #default="{ row }">
                          <a v-if="row.url" :href="row.url" target="_blank" rel="noopener noreferrer" class="src-link">{{ row.page }}</a>
                          <span v-else>{{ row.page }}</span>
                        </template>
                      </el-table-column>
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
            <div class="msg-actions">
              <el-button text size="small" class="act-btn" @click="copyText(m.text)">复制</el-button>
            </div>
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
        ref="inputRef"
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
.empty-link { color: #ff7a45; text-decoration: none; font-weight: 600; }
.empty-link:hover { text-decoration: underline; }

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
.msg-actions {
  display: flex; gap: 4px; margin-top: 4px;
  opacity: 0; transition: opacity .15s ease; pointer-events: none;
}
.msg-row:hover .msg-actions { opacity: 1; pointer-events: auto; }
.act-btn { padding: 2px 8px; height: auto; font-size: 12px; color: #8a94a6; }
.act-btn:hover { color: #ff7a45; }
.src-link { color: #ff7a45; text-decoration: none; word-break: break-all; }
.src-link:hover { text-decoration: underline; }
</style>