<script setup>
/**
 * 问答控制台（Apple Messages 式）
 * 功能：建议问题 / 停止生成 / 重新生成 / 编辑上一条 / 导出对话 / 逐句引用 /
 *      来源卡片（可展开、可复制）/ 反思过程 / 本地反馈 / 键盘操作 / 移动端自适应。
 *
 * 生命周期约定（本批重点）：
 * - 在途请求由 useChatQuery 的模块级 controller 统一管理，切页/清空/登出/401 都能停；
 * - 每次请求带 generation，旧请求回来时 generation 不一致 → 直接丢弃，
 *   避免"切页后旧回答落到新会话"或"旧 finally 把新请求的 loading 置 false"；
 * - 停止由 UI 侧统一插入可重试占位，响应侧不再各自 push。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Bottom, ChatDotRound, ChatLineRound, CircleClose, Delete, Download, InfoFilled,
  FolderOpened, MagicStick, MoreFilled, Promotion, Search, UploadFilled, VideoPause,
} from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import SegmentedControl from '../components/SegmentedControl.vue'
import InlineText from '../components/InlineText.vue'
import AnswerMeta from '../components/AnswerMeta.vue'
import TraceDetails from '../components/TraceDetails.vue'
import { api, CANCELLED_CODE, friendlyError } from '../api'
import { useAppStore } from '../stores/app'
import { useKbs } from '../composables/useKbs'
import { focusComposerTick } from '../composables/useUi'
import { beginQuery, abortQuery, finishQuery, isCurrent } from '../composables/useChatQuery'
import { buildHistory, findLastUserIndex } from '../utils/chat'
import { hostOf, safeHttpUrl } from '../utils/url'
import { parseMarkdown } from '../utils/markdown'
import { formatClock, toNumber } from '../utils/format'
import { hasEvidence, shouldShowAnswerMeta } from '../utils/answerView'
import { agentFallbackNotice } from '../utils/agentMode'

const store = useAppStore()
const { kbs, load: loadKbs } = useKbs()

const chat = computed(() => store.chat)
const debug = ref(false)
const mode = ref('reflect')   // P-D：reflect | auto | agentic（服务端默认 reflect）
// 服务端实际是否装配 AgentRuntime：null=未知（旧服务/探测失败，不误报），false=未开启。
const agentEnabled = ref(null)
// 服务端 web.enabled（默认是否联网）；null=旧服务未知。仅用于把 tooltip 说准：
// 默认关闭时开启仍需服务端配置 Tavily Key 才会外发，不能直接宣称“会发送”。
const webEnabled = ref(null)
const webSearch = computed({
  get: () => store.webSearch,
  set: (v) => { store.webSearch = v },
})
const webTip = computed(() => {
  if (webEnabled.value === false) {
    return webSearch.value
      ? '联网已开启：服务端默认关闭，配置 Tavily Key 后才会外发'
      : '仅检索本地知识库（服务端默认关闭联网）'
  }
  return webSearch.value
    ? '联网搜索已开启（问题文本会发送至 Tavily）'
    : '仅检索本地知识库'
})

const bodyRef = ref(null)
const inputRef = ref(null)
const atBottom = ref(true)
const docNames = ref([])
const kbDocs = ref(0)
const expandedSources = ref({})
const feedback = ref({})
let composing = false
let pendingInsertAt = null   // 停止时把占位插回原位置（重生成场景）
// 在途请求发起时快照的知识库：切库后提示“这条仍按旧库回答”，并给消息打归属。
let inflightKb = null        // { id, name } | null

const currentKb = computed(() => kbs.value.find((k) => k.kb_id === store.kbId) || null)
const emptyKb = computed(() => kbDocs.value === 0)
const messages = computed(() => chat.value.messages)
const lastUserIndex = computed(() => findLastUserIndex(messages.value))
const lastUserMessage = computed(() => messages.value[lastUserIndex.value] || null)

/* ---------------- 数据加载 ---------------- */
/** 读取服务端实际装配态；失败/旧服务无 features 时保持未知，不误报“Agent 未开启”。 */
async function loadServerFeatures() {
  const res = await api('/api/health', { silent: true, timeout: 8000 })
  if (res.code === 0 && res.data && res.data.features) {
    const features = res.data.features
    agentEnabled.value = features.agent_enabled !== false
    if (Object.prototype.hasOwnProperty.call(features, 'web_enabled')) {
      webEnabled.value = features.web_enabled !== false
    }
  }
}

onMounted(async () => {
  await loadKbs()
  // 库列表就绪后再判定持久化的 kbId：无权/已删除时先回退，避免先发一次注定 40401 的请求
  const moved = ensureKbAccessible()
  if (!moved) loadKbDocs()
  loadFeedback()
  loadServerFeatures()
  // 切页/刷新回来时恢复到底部：否则长对话停在顶部，且“回到底部”按钮因 atBottom=true 而隐藏
  if (messages.value.length) nextTick(() => scrollToBottom())
})

onUnmounted(() => {
  // 离开页面即放弃在途请求：与“停止”一致留下可重试占位，否则用户回来只看到
  // 自己的问题、没有任何“已中断”的解释（S3）。
  const wasLoading = chat.value.loading
  const at = pendingInsertAt
  abortQuery()
  chat.value.loading = false
  pendingInsertAt = null
  if (wasLoading) {
    insertMessage({
      id: newId(), role: 'assistant', text: '', result: null,
      interrupted: true, time: new Date(),
      kbId: inflightKb && inflightKb.id,
      kbName: inflightKb && inflightKb.name,
    }, at)
  }
  inflightKb = null
})

/** 上次选中的库可能已被删除或无权访问 → 回落到 default，避免空白列表与无效请求。
 *  @returns {boolean} 是否发生了回退（调用方据此决定是否立即加载，避免重复请求） */
function ensureKbAccessible() {
  if (kbs.value.length && !kbs.value.some((k) => k.kb_id === store.kbId)) {
    store.setKbId('default')
    store.pushInfo('上次使用的知识库不可用，已切回默认库')
    return true
  }
  return false
}

/** 侧栏/命令面板/本页下拉任一入口切库都会触发：文档数、建议问题、空态同步刷新。 */
watch(
  () => store.kbId,
  () => { loadKbDocs() },
  { immediate: true },
)

async function loadKbDocs() {
  const kbId = store.kbId
  // 列表尚未就绪或该库不可访问：等 onMounted 回退/切库后再加载，避免越权请求
  if (!kbs.value.some((k) => k.kb_id === kbId)) return
  const res = await api(`/api/documents?page=1&size=20&kb_id=${encodeURIComponent(kbId)}`, { silent: true })
  if (kbId !== store.kbId) return   // 快速切库：丢弃过期响应
  if (res.code === 0) {
    kbDocs.value = res.data.total || 0
    docNames.value = (res.data.items || []).map((d) => d.filename.replace(/\.[^.]+$/, ''))
  } else {
    kbDocs.value = 0
    docNames.value = []
  }
}

function onKbChange() {
  // v-model 已写入 store.kbId，但 setKbId 负责持久化到 localStorage；
  // 不调用它会导致刷新后回到上一次写入的库（选中态与持久态不一致）。
  store.setKbId(store.kbId)
  const name = currentKb.value ? currentKb.value.name : store.kbId
  store.pushInfo(`已切换到「${name}」`)
  if (chat.value.loading && inflightKb && inflightKb.id !== store.kbId) {
    // 请求已按旧库发出：必须让用户知道这条回答不属于当前库（S3）。
    store.pushInfo(`在途问题仍按「${inflightKb.name || inflightKb.id}」回答`)
  }
  nextTick(() => inputRef.value && inputRef.value.focus())
}

/* ---------------- 建议问题 ---------------- */
const suggestions = computed(() => {
  const d = docNames.value
  const out = []
  if (d[0]) out.push(`总结《${d[0]}》的核心要点`)
  if (d[0]) out.push(`《${d[0]}》里有哪些关键规定？`)
  if (d[1]) out.push(`《${d[1]}》讲了什么？`)
  if (!out.length) out.push('这个知识库可以回答哪些问题？')
  return out.slice(0, 3)
})

function useSuggestion(text) {
  chat.value.query = text
  nextTick(() => inputRef.value && inputRef.value.focus())
}

/* ---------------- 提问 / 停止 / 重试 ---------------- */
function newId() {
  return `m-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function insertMessage(message, at) {
  if (at == null) messages.value.push(message)
  else messages.value.splice(Math.min(at, messages.value.length), 0, message)
}

/**
 * @param {string} q
 * @param {{appendUser?: boolean, insertAt?: number|null, historyOverride?: Array|null,
 *          kbId?: string, kbName?: string}} options
 */
async function runQuery(q, options = {}) {
  const { appendUser = true, insertAt = null, historyOverride = null } = options
  if (chat.value.loading) {
    store.pushInfo('正在生成回答，请先停止后再重试')
    return
  }
  const question = String(q || '').trim()
  if (!question) { store.pushError('请输入问题'); return }

  // 发起时快照知识库：在途切库/重试都不能把回答错记到当前库（S3）。
  const requestKbId = options.kbId || store.kbId
  const requestKbName = options.kbName
    || (requestKbId === store.kbId && currentKb.value
      ? currentKb.value.name
      : ((kbs.value.find((k) => k.kb_id === requestKbId) || {}).name || requestKbId))
  const history = historyOverride || buildHistory(messages.value)
  if (appendUser) {
    messages.value.push({
      id: newId(), role: 'user', text: question, time: new Date(),
      kbId: requestKbId, kbName: requestKbName,
    })
  }
  chat.value.query = ''
  atBottom.value = true
  scrollToBottom()

  const token = beginQuery()
  pendingInsertAt = insertAt
  chat.value.loading = true
  inflightKb = { id: requestKbId, name: requestKbName }
  // 用“发起时”的模式生成提示：用户回答后切到别的模式，不应改写这条消息的语义。
  const requestedMode = mode.value
  try {
    const res = await api('/api/query', {
      method: 'POST',
      signal: token.signal,
      body: JSON.stringify({
        query: question,
        kb_id: requestKbId,
        debug: debug.value,
        web_search: webSearch.value,
        mode: requestedMode,
        history,
        session_id: store.ensureSessionId(),
      }),
    })
    // 被停止/清空/登出/新请求取代：旧响应一律丢弃
    if (!isCurrent(token.generation)) return
    if (res.code === CANCELLED_CODE) return   // 停止占位由 stop() 统一插入

    const okAnswer = res.code === 0
    const result = { ...(res.data || {}), code: res.code }
    // 21003 的具体澄清问句在 data.answer；message 只是通用标题，不能丢具体问句。
    const errorText = !okAnswer && res.code === 21003 && res.data && res.data.answer
      ? String(res.data.answer)
      : friendlyError(res.code, res.message)
    insertMessage({
      id: newId(),
      role: 'assistant',
      text: okAnswer ? ((res.data && res.data.answer) || '') : errorText,
      result,
      // D01：Agent 选中但服务端未开启/回退时，消息旁必须可见提示，不能只靠本地分段态。
      agentNotice: agentFallbackNotice(result, requestedMode),
      kbId: requestKbId,
      kbName: requestKbName,
      time: new Date(),
      error: !okAnswer,
    }, insertAt)
  } catch (e) {
    if (!isCurrent(token.generation)) return
    insertMessage({
      id: newId(),
      role: 'assistant',
      text: friendlyError(-1, String((e && e.message) || e)),
      result: { code: -1, message: String((e && e.message) || e) },
      kbId: requestKbId,
      kbName: requestKbName,
      time: new Date(),
      error: true,
    }, insertAt)
  } finally {
    finishQuery(token)
    if (isCurrent(token.generation)) {
      chat.value.loading = false
      pendingInsertAt = null
      inflightKb = null
      if (atBottom.value) scrollToBottom()
    }
  }
}

function ask() {
  runQuery(chat.value.query)
}

/** 停止生成：中断请求，并在原位置插入可重试的占位（不在响应侧各自 push，避免竞态）。 */
function stop() {
  if (!chat.value.loading) return
  const at = pendingInsertAt
  abortQuery()
  chat.value.loading = false
  pendingInsertAt = null
  insertMessage({
    id: newId(), role: 'assistant', text: '', result: null, interrupted: true, time: new Date(),
    kbId: inflightKb && inflightKb.id, kbName: inflightKb && inflightKb.name,
  }, at)
  inflightKb = null
  store.pushInfo('已停止生成')
  if (atBottom.value) scrollToBottom()
}

/**
 * 重新生成：只重跑该回答对应的提问。
 * - loading 中直接拒绝，不能先 splice 再静默失败；
 * - 历史只取该问题之前的消息，且排除"当前问题"本身（它已在 payload.query）；
 * - 新回答插回原 index，而不是 append 到末尾。
 */
function regenerate(index) {
  if (chat.value.loading) {
    store.pushInfo('请先停止当前生成，再重新生成')
    return
  }
  const target = messages.value[index]
  if (!target || target.role !== 'assistant') return
  const question = findQuestionBefore(index)
  if (!question) return
  const historyOverride = buildHistory(messages.value, { beforeIndex: index, excludeTrailingUser: true })
  // 重新生成必须回到原回答所属的库；否则切库后重试会悄悄查错库（S3）。
  const kbId = target.kbId || store.kbId
  const kbName = target.kbName
    || ((kbs.value.find((k) => k.kb_id === kbId) || {}).name || kbId)
  messages.value.splice(index, 1)
  runQuery(question, { appendUser: false, insertAt: index, historyOverride, kbId, kbName })
}

function findQuestionBefore(index) {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (messages.value[i].role === 'user') return messages.value[i].text
  }
  return ''
}

function retry(index) {
  regenerate(index)
}

function onEnter(e) {
  if (e.isComposing || composing) return   // IME 组合期（确认候选词）不发送
  if (e.metaKey || e.ctrlKey) return       // ⌘Enter 交给单独处理器
  ask()
}

function onComposerKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault()
    ask()
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key === '.') {
    e.preventDefault()
    if (chat.value.loading) stop()
    return
  }
  if (e.key === 'ArrowUp' && !chat.value.query && !chat.value.loading && lastUserIndex.value >= 0) {
    e.preventDefault()
    editUserMessage(lastUserIndex.value)
  }
}

/* ---------------- 消息操作 ---------------- */
function clearChat() {
  abortQuery()
  chat.value.loading = false
  pendingInsertAt = null
  inflightKb = null
  chat.value.messages = []
  chat.value.query = ''
  store.resetSession()
  feedback.value = {}
  store.pushInfo('已开始新会话')
}

async function copyText(text, tip = '已复制到剪贴板') {
  try {
    await navigator.clipboard.writeText(text || '')
    store.pushInfo(tip)
  } catch {
    store.pushError('复制失败，请手动选择复制')
  }
}

function editUserMessage(index) {
  const msg = messages.value[index]
  if (!msg || msg.role !== 'user') return
  chat.value.query = msg.text || ''
  nextTick(() => {
    if (inputRef.value) inputRef.value.focus()
  })
}

function vote(msg, dir) {
  const next = feedback.value[msg.id] === dir ? null : dir
  const map = { ...feedback.value }
  if (next) map[msg.id] = next
  else delete map[msg.id]
  feedback.value = map
  persistFeedback()
  if (next) store.pushInfo(next === 'up' ? '感谢反馈：回答有帮助' : '感谢反馈：回答需改进（记录在本机）')
}

function loadFeedback() {
  try {
    feedback.value = JSON.parse(localStorage.getItem('rag_feedback') || '{}') || {}
  } catch { feedback.value = {} }
}
function persistFeedback() {
  try {
    localStorage.setItem('rag_feedback', JSON.stringify(feedback.value))
  } catch { /* ignore */ }
}

/* ---------------- 渲染辅助 ---------------- */
const blockCache = new Map()
function blocks(text) {
  const key = String(text || '')
  if (blockCache.has(key)) return blockCache.get(key)
  const parsed = parseMarkdown(key)
  if (blockCache.size > 60) blockCache.clear()
  blockCache.set(key, parsed)
  return parsed
}

function sourceCount(message) {
  const sources = message && message.result && message.result.sources
  return Array.isArray(sources) ? sources.length : 0
}

function sourceScore(source) {
  const s = toNumber(source && source.score, 0)
  return Math.min(Math.max(s, 0), 1)
}

function isExpanded(msgId, i) {
  return Boolean(expandedSources.value[`${msgId}-${i}`])
}
function toggleSource(msgId, i) {
  const key = `${msgId}-${i}`
  expandedSources.value = { ...expandedSources.value, [key]: !expandedSources.value[key] }
}

function sourceLabel(source) {
  return source.source_type === 'web' ? '网页' : '知识库'
}

/** 网页来源没有页码：不要把 URL 当“第 x 页”渲染。 */
function sourceMeta(source) {
  if (!source || source.source_type === 'web') return ''
  return source.page ? `第 ${source.page} 页` : ''
}

function sourceHost(source) {
  return source && source.source_type === 'web' ? hostOf(source.url) : ''
}

/** 只放行 http/https，杜绝 javascript:/data: 之类 scheme。 */
function sourceLink(source) {
  return safeHttpUrl(source && source.url)
}

function jumpToSources(msgId) {
  atBottom.value = true
  const el = document.getElementById(`sources-${msgId}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/* ---------------- 导出 ---------------- */
function exportChat() {
  if (!messages.value.length) { store.pushInfo('当前没有可导出的对话'); return }
  const kbName = currentKb.value ? currentKb.value.name : store.kbId
  const lines = ['# Agentic RAG 对话导出', '', `- 导出时间：${new Date().toLocaleString('zh-CN')}`, `- 知识库：${kbName}`, '']
  for (const m of messages.value) {
    if (m.role === 'user') {
      lines.push(`## 问：${m.text}`, '')
    } else if (m.interrupted) {
      lines.push('## 答（已停止生成）', '')
    } else {
      lines.push('## 答', '', m.text || '', '')
      const sources = (m.result && m.result.sources) || []
      if (sources.length) {
        lines.push('**引用来源**', '')
        sources.forEach((s, i) => {
          lines.push(`${i + 1}. ${s.filename || '未知文件'}${s.page ? ` · 第 ${s.page} 页` : ''}${s.url ? ` · ${s.url}` : ''}`)
        })
        lines.push('')
      }
    }
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `rag-chat-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.md`
  a.click()
  URL.revokeObjectURL(url)
  store.pushInfo('对话已导出为 Markdown')
}

function onMenu(command) {
  if (command === 'export') exportChat()
  else if (command === 'clear') clearChat()
}

/* ---------------- 滚动 ---------------- */
const SCROLL_THRESHOLD = 72

function onScroll() {
  const el = bodyRef.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD
}

function scrollToBottom(smooth = false) {
  nextTick(() => {
    const el = bodyRef.value
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  })
}

watch(
  () => [messages.value.length, chat.value.loading],
  () => { if (atBottom.value) scrollToBottom() },
)

// 命令面板「新对话」后把焦点交还输入框
watch(focusComposerTick, () => nextTick(() => inputRef.value && inputRef.value.focus()))

const canSend = computed(() => Boolean(chat.value.query.trim()) && !chat.value.loading)
</script>

<template>
  <div class="chat-page">
    <header class="chat-head">
      <PageHeader
        title="问答控制台"
        :subtitle="`${currentKb ? currentKb.name : store.kbId} · ${kbDocs} 份文档 · 回答均带引用来源`"
      >
        <template #actions>
          <el-select v-model="store.kbId" class="kb-select" placeholder="知识库" @change="onKbChange">
            <el-option v-for="k in kbs" :key="k.kb_id" :label="k.name" :value="k.kb_id" />
          </el-select>

          <SegmentedControl
            v-model="debug"
            :options="[{ value: false, label: '简洁' }, { value: true, label: '反思过程' }]"
            size="sm"
          />

          <SegmentedControl
            v-model="mode"
            :options="[
              { value: 'auto', label: '自动' },
              { value: 'reflect', label: '反思' },
              { value: 'agentic', label: 'Agent' },
            ]"
            size="sm"
          />
          <span
            v-if="agentEnabled === false"
            class="mode-off"
            title="服务端未启用 Agent（orchestration.agent.enabled=false）"
          >Agent 未开启</span>

          <el-tooltip
            :content="webTip"
            placement="bottom"
          >
            <button
              type="button"
              class="web-toggle"
              :class="{ 'is-on': webSearch }"
              :aria-pressed="webSearch"
              @click="webSearch = !webSearch"
            >
              <el-icon><Search /></el-icon>
              <span>联网</span>
            </button>
          </el-tooltip>

          <el-dropdown trigger="click" @command="onMenu">
            <button type="button" class="btn-icon" aria-label="更多操作">
              <el-icon><MoreFilled /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="export" :disabled="!messages.length">
                  <el-icon><Download /></el-icon>导出对话
                </el-dropdown-item>
                <el-dropdown-item command="clear" :disabled="!messages.length" divided>
                  <el-icon><Delete /></el-icon>清空对话
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </PageHeader>
    </header>

    <div ref="bodyRef" class="chat-scroll" @scroll.passive="onScroll">
      <div class="chat-inner" :class="{ 'chat-inner--empty': !messages.length }">
        <!-- 空态：建议问题一键填入 -->
        <EmptyState
          v-if="!messages.length"
          :icon="ChatLineRound"
          title="基于知识库的智能问答"
          :description="emptyKb
            ? '当前知识库还没有文档。先上传文档，答案才有据可依。'
            : '直接提问，或从下面的建议开始。回答会标注引用编号与已证率。'"
        >
          <template #default>
            <router-link v-if="emptyKb" to="/documents">
              <el-button type="primary">
                <el-icon style="margin-right: 6px"><UploadFilled /></el-icon>上传第一份文档
              </el-button>
            </router-link>
            <template v-else>
              <button
                v-for="s in suggestions"
                :key="s"
                type="button"
                class="suggestion"
                @click="useSuggestion(s)"
              >
                <el-icon><MagicStick /></el-icon>
                <span>{{ s }}</span>
              </button>
            </template>
          </template>
        </EmptyState>

        <template v-for="(m, index) in messages" :key="m.id">
          <!-- 用户消息 -->
          <div v-if="m.role === 'user'" class="msg msg--user">
            <div class="msg-body">
              <div class="bubble--user">{{ m.text }}</div>
              <div class="msg-tools">
                <span class="msg-time">{{ formatClock(m.time) }}</span>
                <button type="button" class="tool" @click="copyText(m.text)">复制</button>
                <button type="button" class="tool" @click="editUserMessage(index)">编辑</button>
              </div>
            </div>
          </div>

          <!-- 助手消息 -->
          <div v-else class="msg msg--ai">
            <span class="ai-avatar" aria-hidden="true"><el-icon><ChatDotRound /></el-icon></span>
            <div class="msg-body">
              <!-- 回答所属库与当前库不一致：切库/重试后必须显式标注，避免张冠李戴（S3） -->
              <div v-if="m.kbId && m.kbId !== store.kbId" class="msg-kb-note" role="note">
                <el-icon><FolderOpened /></el-icon>
                <span>本回答来自「{{ m.kbName || m.kbId }}」，当前已切换到「{{ currentKb ? currentKb.name : store.kbId }}」</span>
              </div>
              <!-- Agent 关闭/回退的可见提示（D01）：不依赖 debug，错误分支同样显示 -->
              <div
                v-if="m.agentNotice"
                class="agent-notice"
                :class="`agent-notice--${m.agentNotice.kind}`"
                role="status"
              >
                <el-icon><InfoFilled /></el-icon>
                <span>{{ m.agentNotice.text }}</span>
              </div>

              <!-- 中断 -->
              <div v-if="m.interrupted" class="interrupted">
                <el-icon><VideoPause /></el-icon>
                <span>已停止生成</span>
                <button type="button" class="tool tool--strong" @click="retry(index)">重新生成</button>
              </div>

              <!-- 失败 -->
              <div v-else-if="m.error || (m.result && m.result.code !== 0)" class="answer answer--error">
                <p class="error-title">
                  <el-icon><CircleClose /></el-icon>
                  {{ (m.result && (m.result.code === 21003 || m.result.need_clarify)) ? '需要澄清' : '未能回答' }}
                </p>
                <p class="error-text">{{ m.text }}</p>
                <button type="button" class="tool tool--strong" @click="retry(index)">重试</button>
                <AnswerMeta v-if="m.result" :result="m.result" :show-verification="false" />
                <TraceDetails v-if="m.result" :result="m.result" :debug="debug" />
              </div>

              <!-- 正常回答 -->
              <div v-else class="answer">
                <div v-if="!blocks(m.text).length" class="answer-empty muted">（空回答）</div>
                <template v-for="(b, bi) in blocks(m.text)" :key="bi">
                  <h3 v-if="b.type === 'h1' || b.type === 'h2' || b.type === 'h3' || b.type === 'h4'" class="md-h">
                    <InlineText :text="b.text" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" />
                  </h3>
                  <ul v-else-if="b.type === 'ul'" class="md-list">
                    <li v-for="(it, ii) in b.items" :key="ii"><InlineText :text="it" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" /></li>
                  </ul>
                  <ol v-else-if="b.type === 'ol'" class="md-list">
                    <li v-for="(it, ii) in b.items" :key="ii"><InlineText :text="it" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" /></li>
                  </ol>
                  <blockquote v-else-if="b.type === 'quote'" class="md-quote">
                    <InlineText :text="b.text" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" />
                  </blockquote>
                  <pre v-else-if="b.type === 'code'" class="md-code"><code>{{ b.text }}</code></pre>
                  <div v-else-if="b.type === 'table'" class="md-table-wrap">
                    <table class="md-table">
                      <thead v-if="b.headers && b.headers.length">
                        <tr>
                          <th v-for="(h, hi) in b.headers" :key="hi">
                            <InlineText :text="h" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" />
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(row, ri) in b.rows" :key="ri">
                          <td v-for="(cell, ci) in row" :key="ci">
                            <InlineText :text="cell" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" />
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <hr v-else-if="b.type === 'hr'" class="md-hr" />
                  <p v-else class="md-p"><InlineText :text="b.text" :cite-count="sourceCount(m)" @cite="jumpToSources(m.id)" /></p>
                </template>

                <AnswerMeta
                  v-if="shouldShowAnswerMeta(m.result)"
                  :result="m.result"
                  :show-verification="hasEvidence(m.result)"
                />

                <!-- 来源卡片 -->
                <div v-if="m.result && m.result.sources && m.result.sources.length" :id="`sources-${m.id}`" class="sources">
                  <p class="sources-title">
                    引用来源
                    <span class="muted">{{ m.result.sources.length }} 条 · 点击正文编号可跳转</span>
                  </p>
                  <article v-for="(s, si) in m.result.sources" :key="si" class="source">
                    <div class="source-head">
                      <span class="source-index">[{{ si + 1 }}]</span>
                      <span class="source-file truncate" :title="s.filename">{{ s.filename || '未知来源' }}</span>
                      <el-tag size="small" :type="s.source_type === 'web' ? 'info' : 'success'" effect="plain">
                        {{ sourceLabel(s) }}
                      </el-tag>
                      <span v-if="sourceMeta(s)" class="source-page">{{ sourceMeta(s) }}</span>
                      <span v-else-if="sourceHost(s)" class="source-page">{{ sourceHost(s) }}</span>
                      <span class="source-score" :title="`相关度 ${sourceScore(s).toFixed(3)}`">
                        <span class="score-track"><i :style="{ width: `${sourceScore(s) * 100}%` }" /></span>
                        <b>{{ sourceScore(s).toFixed(2) }}</b>
                      </span>
                    </div>
                    <p class="source-chunk" :class="{ 'is-expanded': isExpanded(m.id, si) }">{{ s.chunk }}</p>
                    <div class="source-foot">
                      <button
                        v-if="(s.chunk || '').length > 160"
                        type="button"
                        class="tool"
                        @click="toggleSource(m.id, si)"
                      >{{ isExpanded(m.id, si) ? '收起' : '展开全文' }}</button>
                      <button type="button" class="tool" @click="copyText(s.chunk, '已复制原文片段')">复制片段</button>
                      <a
                        v-if="sourceLink(s)"
                        :href="sourceLink(s)"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="tool"
                      >打开原文</a>
                    </div>
                  </article>
                </div>

                <!-- 反思过程 / Agent 计划（正常回答与错误分支共用） -->
                <TraceDetails v-if="m.result" :result="m.result" :debug="debug" />
              </div>

              <div v-if="!m.interrupted" class="msg-tools msg-tools--ai">
                <span class="msg-time">{{ formatClock(m.time) }}</span>
                <button type="button" class="tool" @click="copyText(m.text)">复制</button>
                <button type="button" class="tool" @click="regenerate(index)">重新生成</button>
                <span class="vote">
                  <button
                    type="button"
                    class="tool tool--icon"
                    :class="{ 'is-on': feedback[m.id] === 'up' }"
                    aria-label="回答有帮助"
                    @click="vote(m, 'up')"
                  >👍</button>
                  <button
                    type="button"
                    class="tool tool--icon"
                    :class="{ 'is-on': feedback[m.id] === 'down' }"
                    aria-label="回答需改进"
                    @click="vote(m, 'down')"
                  >👎</button>
                </span>
              </div>
            </div>
          </div>
        </template>

        <!-- 生成中 -->
        <div v-if="chat.loading" class="msg msg--ai">
          <span class="ai-avatar" aria-hidden="true"><el-icon><ChatDotRound /></el-icon></span>
          <div class="typing" role="status" aria-label="正在生成回答">
            <span /><span /><span />
            <em>正在检索并组织答案…</em>
          </div>
        </div>
      </div>
    </div>

    <transition name="fade">
      <button v-if="!atBottom" type="button" class="to-bottom" aria-label="回到底部" @click="scrollToBottom(true)">
        <el-icon><Bottom /></el-icon>
      </button>
    </transition>

    <p
      v-if="agentEnabled === false && mode === 'agentic'"
      class="agent-inline-hint"
      role="status"
    >
      <el-icon><InfoFilled /></el-icon>
      服务端未启用 Agent，本次将按反思模式回答。
    </p>

    <footer class="chat-composer">
      <div class="composer" :class="{ 'is-busy': chat.loading }">
        <el-input
          ref="inputRef"
          v-model="chat.query"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          resize="none"
          maxlength="2000"
          placeholder="输入问题，Enter 发送；Shift + Enter 换行"
          aria-label="输入问题"
          @keydown="onComposerKeydown"
          @keydown.enter.exact.prevent="onEnter"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <div class="composer-bar">
          <div class="composer-hints">
            <span class="hint"><span class="kbd">↵</span> 发送</span>
            <span class="hint"><span class="kbd">⇧↵</span> 换行</span>
            <span class="hint"><span class="kbd">⌘.</span> 停止</span>
          </div>
          <span class="count">{{ chat.query.length }}/2000</span>
          <el-button v-if="chat.loading" class="send-btn" @click="stop">
            <el-icon style="margin-right: 5px"><VideoPause /></el-icon>停止
          </el-button>
          <el-button v-else type="primary" class="send-btn" :disabled="!canSend" @click="ask">
            <el-icon style="margin-right: 5px"><Promotion /></el-icon>发送
          </el-button>
        </div>
      </div>
      <p class="composer-note muted">
        回答由检索结果与模型生成，请以引用原文为准；开启联网会将问题发送至 Tavily，需服务端配置搜索服务。
      </p>
    </footer>
  </div>
</template>

<style scoped>
.chat-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background:
    radial-gradient(900px 400px at 12% 0%, var(--accent-soft), transparent 62%),
    var(--bg);
}
html[data-theme="dark"] .chat-page { background: var(--bg); }

/* ---------- 顶部 ---------- */
.chat-head { padding: 20px 28px 10px; flex: none; }
.kb-select { width: 200px; }
.web-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: all var(--dur) var(--ease);
}
.web-toggle:hover { border-color: var(--accent); color: var(--accent); }
.web-toggle.is-on { background: var(--accent-soft); border-color: var(--accent-soft-2); color: var(--accent); }
.mode-off {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border-radius: var(--radius-pill);
  background: var(--fill-1);
  color: var(--text-tertiary);
  font-size: 11px;
  white-space: nowrap;
}
.agent-inline-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  margin: 0 28px;
  padding: 7px 12px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm, 8px);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
}

/* ---------- 消息区 ---------- */
.chat-scroll { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; padding: 6px 28px 4px; }
.chat-inner--empty { flex: 1; justify-content: center; }
.chat-inner {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding-bottom: 18px;
}

.msg { display: flex; gap: 10px; align-items: flex-start; animation: fade-up 300ms var(--ease) both; }
.msg--user { justify-content: flex-end; }
.msg-body { display: flex; flex-direction: column; max-width: min(76%, 680px); min-width: 0; }
.msg--user .msg-body { align-items: flex-end; }


.msg-kb-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: flex-start;
  margin-bottom: 7px;
  padding: 5px 9px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm, 8px);
  background: var(--fill-1);
  color: var(--text-tertiary);
  font-size: 11.5px;
}
.msg-kb-note .el-icon { font-size: 12px; }
.agent-notice {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 7px;
  padding: 7px 10px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm, 8px);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.agent-notice .el-icon { margin-top: 2px; flex: none; }
.agent-notice--disabled { border-color: var(--hairline); }
.agent-notice--fallback { border-color: var(--warning); color: var(--warning); }

.ai-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  margin-top: 2px;
  border-radius: 50%;
  font-size: 15px;
  color: #fff;
  background: var(--accent-gradient);
  box-shadow: 0 4px 12px var(--accent-ring);
}

.bubble--user {
  padding: 10px 15px;
  border-radius: 18px;
  border-bottom-right-radius: 6px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: 0 4px 14px var(--accent-ring);
}

.answer {
  padding: 14px 16px;
  border-radius: 18px;
  border-bottom-left-radius: 6px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  box-shadow: var(--shadow-1);
  font-size: 14px;
  line-height: 1.75;
  color: var(--text);
  min-width: 0;
}
.answer--error {
  border-color: var(--danger-soft);
  background: var(--danger-soft);
  color: var(--danger);
}
.error-title { display: flex; align-items: center; gap: 6px; margin: 0 0 6px; font-weight: 600; font-size: 13px; }
.error-text { margin: 0; font-size: 13px; line-height: 1.65; }
.answer-empty { padding: 4px 0; }

.md-p { margin: 0 0 10px; }
.md-p:last-child { margin-bottom: 0; }
.md-h { margin: 14px 0 8px; font-size: 14.5px; font-weight: 650; letter-spacing: -0.01em; }
.md-h:first-child { margin-top: 0; }
.md-list { margin: 0 0 10px; padding-left: 20px; }
.md-list li { margin: 3px 0; }
.md-quote {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-left: 3px solid var(--accent-soft-2);
  background: var(--fill-1);
  border-radius: 0 var(--radius-xs) var(--radius-xs) 0;
  color: var(--text-secondary);
}
.md-code {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  border: 1px solid var(--hairline);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}
.md-hr { border: none; border-top: 1px solid var(--hairline); margin: 12px 0; }
/* 表格：横向可滚动，单元格允许换行，避免长报错信息把气泡撑爆 */
.md-table-wrap {
  margin: 10px 0;
  /* 气泡宽度由内容决定：给表格一个下限，避免窄表把气泡挤成一条竖条 */
  min-width: min(320px, 100%);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  overflow-x: auto;
}
.md-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: 1.6;
}
.md-table th,
.md-table td {
  min-width: 84px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--hairline);
  border-right: 1px solid var(--hairline);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
.md-table th:last-child,
.md-table td:last-child { border-right: none; }
.md-table tbody tr:last-child td { border-bottom: none; }
.md-table th {
  background: var(--fill-1);
  font-weight: 600;
  white-space: nowrap;
}
.md-table tbody tr:hover td { background: var(--fill-1); }
/* 报错类长标识（StackOverflowError 等）不要被从中间掰断，宁可让外层横向滚动 */
.md-table .inline-code { white-space: nowrap; }

/* 来源 */
.sources { margin-top: 12px; }
.sources-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}
.source {
  padding: 10px 12px;
  margin-bottom: 8px;
  border-radius: var(--radius-md);
  background: var(--fill-1);
  border: 1px solid transparent;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.source:hover { background: var(--surface); border-color: var(--hairline); }
.source-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.source-index { font-family: var(--font-mono); font-size: 11px; color: var(--accent); font-weight: 600; }
.source-file { font-size: 12.5px; font-weight: 600; max-width: 240px; }
.source-page { font-size: 11px; color: var(--text-tertiary); }
.source-score { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; flex: none; }
.score-track {
  width: 56px;
  height: 4px;
  border-radius: 999px;
  background: var(--fill-2);
  overflow: hidden;
}
.score-track i { display: block; height: 100%; border-radius: 999px; background: var(--accent); }
.source-score b {
  font-size: 10.5px;
  color: var(--text-tertiary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.source-chunk {
  margin: 8px 0 4px;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.source-chunk.is-expanded { display: block; -webkit-line-clamp: unset; }
.source-foot { display: flex; gap: 4px; flex-wrap: wrap; }

/* 工具条 */
.msg-tools {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 160ms var(--ease);
}
.msg:hover .msg-tools,
.msg:focus-within .msg-tools { opacity: 1; }
/* 触屏没有 hover：操作按钮常显，否则永远点不到 */
@media (hover: none) {
  .msg-tools { opacity: 1; }
}
.msg-tools--ai { margin-top: 6px; }
.msg-time { font-size: 10.5px; color: var(--text-quaternary); margin-right: 4px; }
.tool {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 7px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  font-family: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.tool:hover { background: var(--fill-1); color: var(--accent); }
.tool--strong { color: var(--accent); font-weight: 600; }
.tool--icon { font-size: 12px; padding: 0 5px; }
.tool--icon.is-on { background: var(--accent-soft); }
.vote { display: inline-flex; gap: 2px; margin-left: 4px; padding-left: 6px; border-left: 1px solid var(--hairline); }

.interrupted {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  border: 1px dashed var(--hairline-strong);
  background: var(--fill-1);
  color: var(--text-tertiary);
  font-size: 12.5px;
}

.typing {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 14px 16px;
  border-radius: 18px;
  border-bottom-left-radius: 6px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  box-shadow: var(--shadow-1);
}
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.35;
  animation: blink 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: 0.18s; }
.typing span:nth-child(3) { animation-delay: 0.36s; }
.typing em { margin-left: 6px; font-style: normal; font-size: 12px; color: var(--text-tertiary); }
@keyframes blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }

/* 建议问题 */
.suggestion {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 13px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  box-shadow: var(--shadow-1);
  transition: all var(--dur) var(--ease);
}
.suggestion:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
.suggestion .el-icon { color: var(--accent); }

/* 回到底部 */
.to-bottom {
  position: absolute;
  right: 34px;
  bottom: 148px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--hairline);
  border-radius: 50%;
  background: var(--surface);
  color: var(--text-secondary);
  box-shadow: var(--shadow-2);
  cursor: pointer;
  z-index: 5;
}
.to-bottom:hover { color: var(--accent); }
.fade-enter-active, .fade-leave-active { transition: opacity 180ms var(--ease); }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* 输入区 */
.chat-composer {
  flex: none;
  padding: 10px 28px 16px;
  background: linear-gradient(180deg, transparent, var(--bg) 42%);
}
.composer {
  max-width: 860px;
  margin: 0 auto;
  padding: 10px 12px 8px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-2);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.composer:focus-within { border-color: var(--accent-soft-2); box-shadow: 0 0 0 4px var(--accent-soft), var(--shadow-2); }
.composer :deep(.el-textarea__inner) {
  background: transparent;
  padding: 2px 4px 6px;
  box-shadow: none !important;
  font-size: 14px;
  line-height: 1.6;
}
.composer-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 6px;
  border-top: 1px solid var(--hairline);
}
.composer-hints { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
.hint { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-quaternary); }
.count { font-size: 11px; color: var(--text-quaternary); font-variant-numeric: tabular-nums; }
.send-btn { min-width: 88px; }
.composer-note { max-width: 860px; margin: 7px auto 0; text-align: center; font-size: 10.5px; }

@media (max-width: 900px) {
  .chat-head { padding: 14px 16px 6px; }
  .chat-scroll { padding: 4px 14px 0; }
  .chat-composer { padding: 8px 12px 12px; }
  .msg-body { max-width: 88%; }
  .kb-select { width: 160px; }
  .to-bottom { right: 18px; bottom: 150px; }
}
@media (max-width: 640px) {
  .composer-hints { display: none; }
  .kb-select { flex: 1; width: auto; min-width: 0; }
  .chat-head :deep(.head-actions) { width: 100%; }
  .msg-body { max-width: 92%; }
  .ai-avatar { display: none; }
}
</style>
