<script setup>
/**
 * 文档管理（重做）
 * - 拖拽上传 + 串行队列（修复旧版 uploading 从未置 true 导致并发上传/进度互相覆盖）
 * - 搜索 / 类型筛选 / 排序 / 客户端分页（旧版硬编码 size=100，超过 100 份直接不可见）
 * - 行级操作：详情抽屉 / 同名更新（mode=upsert）/ 强制重建（force）/ 删除
 * - 批量删除、只读库提示、空态引导
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  Clock, CopyDocument, Delete, Document, Files, FolderOpened, Lock,
  Refresh, RefreshRight, Search, UploadFilled, View, WarningFilled,
} from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import StatTile from '../components/StatTile.vue'
import { api, friendlyError } from '../api'
import { getBaseUrl } from '../api/base'
import { useAppStore } from '../stores/app'
import { useKbs } from '../composables/useKbs'
import { useIsMobile } from '../composables/useMedia'
import { fileKind, formatDateTime, formatRelative, toNumber } from '../utils/format'
import { confirmDialog } from '../utils/confirm'
// 重组件局部注册：只随本页 chunk 加载，问答首屏不再下载 table/upload/pagination
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElUpload from 'element-plus/es/components/upload/index.mjs'
import ElPagination from 'element-plus/es/components/pagination/index.mjs'

const store = useAppStore()
const { kbs, load: loadKbs } = useKbs()
const isMobile = useIsMobile()   // 窄屏隐藏次要列，避免表格横向溢出

const SUPPORTED_TYPES = 'PDF / Word / Excel / PPT / Markdown / 文本 / 网页 / 邮件 / JSON / CSV / 代码与配置'
const SUPPORTED_ACCEPT = '.pdf,.docx,.xlsx,.pptx,.doc,.xls,.ppt,.md,.txt,.html,.htm,.eml,.msg'
  + ',.json,.jsonl,.csv,.tsv,.py,.js,.mjs,.cjs,.ts,.tsx,.jsx,.java,.go,.rs,.c,.h,.cpp,.hpp,.cs'
  + ',.rb,.php,.kt,.swift,.sh,.sql,.vue,.css,.scss,.yaml,.yml,.toml,.ini,.xml'
const MAX_FILE_SIZE = 50 * 1024 * 1024   // 与服务端 max_file_size_mb: 50 对齐

const files = ref([])
const total = ref(0)
const loading = ref(false)
/** 列表加载失败原因：常驻显示 + 可重试，避免旧数据静默残留让人误判“文档丢了”。 */
const listError = ref('')
const query = ref('')
const kindFilter = ref('all')
const sortBy = ref('recent')
const page = ref(1)
const pageSize = ref(25)
const pageSizes = [25, 50, 100]
const stats = ref({ chunks: 0, latest: 0 })
const selection = ref([])

const queue = ref([])          // 等待上传的 { file, kbId }（kbId 为入队时快照）
const uploading = ref(false)   // 队列是否在跑（真实状态，旧版缺陷在此）
const progress = ref(null)     // { filename, phase, percent }
const activeJob = ref(null)
let currentXhr = null
let mounted = true

const detailOpen = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const pendingAction = ref(null)   // { mode: 'upsert' | 'force', row }
const fileInput = ref(null)

const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'
const currentKb = computed(() => kbs.value.find((k) => k.kb_id === store.kbId) || null)
const readOnly = computed(() => Boolean(currentKb.value && currentKb.value.scope === 'shared' && !isAdmin()))

onMounted(async () => {
  await loadKbs()
  if (kbs.value.length && !kbs.value.some((k) => k.kb_id === store.kbId)) store.setKbId('default')
  refreshDocs()
})
onUnmounted(() => {
  mounted = false
  queue.value = []
  if (currentXhr) currentXhr.abort()
  // 结束在途轮询的 Promise，否则 drain 会一直 await（组件已卸载，无人消费）
  if (pollJobState) pollJobState.cancel()
})

/* ---------------- 列表 ---------------- */
async function refreshDocs({ silent = false } = {}) {
  loading.value = true
  selection.value = []
  const kbId = store.kbId
  try {
    const params = new URLSearchParams({
      page: String(page.value),
      size: String(pageSize.value),
      kb_id: kbId,
      sort: sortBy.value,
    })
    const keyword = query.value.trim()
    if (keyword) params.set('q', keyword)
    if (kindFilter.value !== 'all') params.set('kind', kindFilter.value)
    const res = await api(`/api/documents?${params.toString()}`, { silent: true })
    if (kbId !== store.kbId) return   // 期间切了库：丢弃过期响应
    if (res.code !== 0) {
      // 失败保留上一次数据：清空列表比旧数据更容易让人误判“文档丢了”
      listError.value = friendlyError(res.code, res.message)
      if (!silent) store.pushError(`列表刷新失败：${listError.value}`)
      return
    }
    listError.value = ''
    files.value = (res.data.items || []).map((d) => ({
      ...d,
      created: formatDateTime(d.created_at),
      createdRelative: formatRelative(d.created_at),
      updatedRelative: d.updated_at ? formatRelative(d.updated_at) : '',
      kind: fileKind(d.filename),
    }))
    total.value = Number(res.data.total) || 0
    stats.value = res.data.stats || { chunks: 0, latest: 0 }
    // 删除末页最后一条后回退一页，避免停留在空页
    if (!files.value.length && page.value > 1) {
      page.value -= 1
      await refreshDocs({ silent: true })
    }
  } finally {
    loading.value = false
  }
}

function onKbChange() {
  store.setKbId(store.kbId)
  page.value = 1
  query.value = ''
  pendingAction.value = null   // 文件框未选完就切库：作废旧行的更新/重建意图
  refreshDocs()
}

const kindOptions = [
  { value: 'pdf', label: 'PDF' },
  { value: 'doc', label: 'Word' },
  { value: 'sheet', label: '表格' },
  { value: 'slide', label: '演示' },
  { value: 'md', label: 'Markdown' },
  { value: 'code', label: '代码' },
  { value: 'config', label: '配置' },
  { value: 'text', label: '文本' },
  { value: 'image', label: '图片' },
]

let searchTimer = null
/** 搜索防抖：输入 250ms 后再请求，避免逐字符打服务端。 */
function onSearch() {
  page.value = 1
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => refreshDocs({ silent: true }), 250)
}

/** 每页条数变化：回到第一页重新请求。 */
function onPageSizeChange(size) {
  pageSize.value = size
  page.value = 1
  refreshDocs()
}

/** 类型/排序变化：回到第一页并重新请求（筛选在服务端完成）。 */
function onFilterChange() {
  page.value = 1
  refreshDocs()
}

/* ---------------- 上传（串行队列 + 可取消） ---------------- */
function enqueue(uploadFile) {
  const file = uploadFile && uploadFile.raw ? uploadFile.raw : uploadFile
  if (!file) return
  if (file.size > MAX_FILE_SIZE) {
    store.pushError(`「${file.name}」超过 50MB 限制，未上传`)
    return
  }
  // 入队时快照目标库：即便后续切库，任务仍落到发起时的库（选择器同时会被禁用）
  queue.value.push({ file, kbId: store.kbId })
  drain()
}

/** 串行消费：同一时刻只有一个上传在处理，进度条不再被后来的文件覆盖。 */
async function drain() {
  if (uploading.value) return
  uploading.value = true
  try {
    while (queue.value.length && mounted) {
      const item = queue.value.shift()
      await uploadOne(item)
    }
  } finally {
    uploading.value = false
    if (mounted) progress.value = null
    // 竞态兜底：取消/失败收尾的瞬间可能有新文件入队，此时 enqueue 的 drain() 会因
    // uploading=true 直接返回；这里收尾后再拉一次，避免文件永远躺在队列里。
    if (mounted && queue.value.length) drain()
  }
}

function uploadWithProgress(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    currentXhr = xhr
    xhr.open('POST', getBaseUrl() + '/api/documents/upload')
    xhr.timeout = 2 * 60 * 1000
    const token = localStorage.getItem('auth_token') || ''
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('X-Trace-Id', (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && progress.value) {
        progress.value = { ...progress.value, percent: Math.round((e.loaded / e.total) * 100) }
      }
    }
    xhr.onload = () => {
      try { resolve(JSON.parse(xhr.responseText)) } catch { reject(new Error('服务响应解析失败')) }
    }
    xhr.onerror = () => reject(new Error('网络上传失败'))
    xhr.onabort = () => reject(new Error('已取消上传'))
    xhr.ontimeout = () => reject(new Error('上传超时'))
    xhr.send(form)
  })
}

/**
 * @param {{file: File, kbId: string}} item 入队时的文件与目标库快照
 * @param {'add'|'upsert'} mode
 * @param {{force?: boolean}} options force=true 走强制重建（后端跳过幂等短路）
 */
async function uploadOne(item, mode = 'add', options = {}) {
  const { file, kbId } = item
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', kbId)
  form.append('mode', mode)
  if (options.force) form.append('force', 'true')

  progress.value = { filename: file.name, phase: 'upload', percent: 0 }
  try {
    const res = await uploadWithProgress(form)
    if (!mounted) return
    if (res.code === 40101) {
      // 手动 XHR 绕过了 api() 的统一 401 处理：登出并终止队列，不再逐个重试
      queue.value = []
      store.logout()
      return
    }
    if (res.code !== 0) {
      store.pushError(`上传失败「${file.name}」：${friendlyError(res.code, res.message)}`)
      return
    }
    if (res.data.duplicate) {
      store.pushInfo(`「${file.name}」内容与已有文档相同，已跳过（可用「强制重建」覆盖）`)
      return
    }
    const replaced = res.data.replaced_document_id
    if (options.force) store.pushInfo(`已提交重建「${file.name}」，正在重新解析…`)
    else if (replaced) store.pushInfo(`已提交更新「${file.name}」，新版本入库后替换旧版本`)
    else store.pushInfo(`已提交「${file.name}」，正在入库…`)

    progress.value = { filename: file.name, phase: 'ingest', percent: null }
    const job = await pollJob(res.data.job_id)
    if (!mounted || !job) return
    if (job.status === 'success') {
      store.pushInfo(`「${file.name}」入库完成，共 ${job.chunk_count ?? 0} 个片段`)
    } else if (job.status === 'failed') {
      store.pushError(`处理失败「${file.name}」：请确认格式受支持且内容可解析`)
    }
    // 目标库仍是当前库才刷新列表，避免把别的库的数据刷到当前视图
    if (kbId === store.kbId) await refreshDocs({ silent: true })
  } catch (e) {
    const cancelled = String(e && e.message || '').includes('取消')
    if (!mounted) return
    if (cancelled) store.pushInfo('已取消上传')
    else store.pushError(`上传失败「${file.name}」：${(e && e.message) || '请重试'}`)
  } finally {
    currentXhr = null
  }
}

/**
 * 轮询 Job 直到成功/失败（4 分钟上限）。
 * 关键：返回的 Promise 必须可被 cancelUpload 结束，否则取消后 drain 会永久 await。
 * 用 setTimeout 链而非 setInterval(async)，避免上一次请求未返回时叠加下一轮。
 */
let pollJobState = null

function pollJob(jobId) {
  return new Promise((resolve, reject) => {
    let attempts = 0
    const MAX = Math.floor((4 * 60 * 1000) / 800)
    const state = {
      timer: null,
      cancelled: false,
      cancel() {
        if (state.cancelled) return
        state.cancelled = true
        if (state.timer) clearTimeout(state.timer)
        state.timer = null
        if (pollJobState === state) pollJobState = null
        activeJob.value = null
        reject(new Error('已取消上传'))
      },
    }
    pollJobState = state
    activeJob.value = jobId

    const finish = (value) => {
      if (state.timer) clearTimeout(state.timer)
      state.timer = null
      if (pollJobState === state) pollJobState = null
      activeJob.value = null
      resolve(value)
    }

    const tick = async () => {
      if (state.cancelled || !mounted) return
      attempts += 1
      const res = await api(`/api/jobs/${jobId}`, { silent: true })
      if (state.cancelled || !mounted) return
      if (res.code !== 0) {
        store.pushError(`无法获取入库状态：${friendlyError(res.code, res.message)}`)
        finish(null)
        return
      }
      const status = res.data && res.data.status
      if (status === 'success' || status === 'failed') { finish(res.data); return }
      if (attempts >= MAX) {
        store.pushError('入库耗时过长，请稍后在列表中确认结果')
        finish(null)
        return
      }
      // 页面在后台时降频，回前台自动恢复
      const delay = typeof document !== 'undefined' && document.hidden ? 3000 : 800
      state.timer = setTimeout(tick, delay)
    }
    state.timer = setTimeout(tick, 800)
  })
}

/** 取消当前上传：上传阶段 abort XHR，入库阶段结束轮询；队列清空，drain 随后自然退出。 */
function cancelUpload() {
  queue.value = []
  if (currentXhr) currentXhr.abort()
  if (pollJobState) pollJobState.cancel()
  progress.value = null
}

/* ---------------- 行级操作 ---------------- */
function askReplace(row, mode) {
  if (uploading.value) { store.pushError('已有上传任务进行中，请稍候'); return }
  // 快照行所在库：文件选择框打开期间若切库，更新/重建仍应落到原库
  pendingAction.value = { mode, row, kbId: row.kb_id || store.kbId }
  if (fileInput.value) {
    fileInput.value.value = ''
    fileInput.value.click()
  }
}

/** 更新/重建共用：选完文件后统一校验（大小、同名确认），再走 uploadOne。 */
async function handleReplaceFile(file, action) {
  if (!file || !action) return
  if (uploading.value) { store.pushError('已有上传任务进行中，请稍候'); return }
  if (file.size > MAX_FILE_SIZE) {
    store.pushError(`「${file.name}」超过 50MB 限制`)
    return
  }
  if (file.name !== action.row.filename) {
    const verb = action.mode === 'force' ? '重建' : '更新'
    const ok = await confirmDialog(
      `文件名不一致：原文档为「${action.row.filename}」，所选为「${file.name}」。\n`
      + `继续会把它作为新文档入库，而不是${verb}当前这一行。`,
      action.mode === 'force' ? '重建索引' : '更新文档',
    )
    if (!ok) return
  }
  uploading.value = true
  try {
    await uploadOne({ file, kbId: action.kbId || store.kbId }, 'upsert', { force: action.mode === 'force' })
  } finally {
    uploading.value = false
    progress.value = null
  }
}

function onReplacePicked(e) {
  const file = e.target.files && e.target.files[0]
  const action = pendingAction.value
  pendingAction.value = null   // 无论成功/取消/失败都不残留
  if (!file || !action) return
  handleReplaceFile(file, action)
}

/** 强制重建：同一文件走 force=true（服务端跳过幂等短路，重建向量）。 */
function forceRebuild(row) {
  if (uploading.value) { store.pushError('已有上传任务进行中，请稍候'); return }
  pendingAction.value = { mode: 'force', row, kbId: row.kb_id || store.kbId }
  if (fileInput.value) {
    fileInput.value.value = ''
    fileInput.value.click()
  }
}

function pickForceFile(e) {
  const file = e.target.files && e.target.files[0]
  const action = pendingAction.value
  pendingAction.value = null
  if (!file || !action) return
  handleReplaceFile(file, action)
}

async function openDetail(row) {
  detailLoading.value = true
  detailOpen.value = true
  detail.value = { ...row }
  try {
    const res = await api(`/api/documents/${row.document_id}`)
    if (res.code === 0) detail.value = { ...row, ...res.data, kind: row.kind }
  } finally {
    detailLoading.value = false
  }
}

async function removeDoc(row) {
  const ok = await confirmDialog(`确认删除「${row.filename}」？删除后不可恢复。`, '删除文档')
  if (!ok) return
  const res = await api(`/api/documents/${row.document_id}`, { method: 'DELETE', silent: true })
  if (res.code === 0) {
    store.pushInfo(`已删除「${row.filename}」`)
    refreshDocs()
  } else {
    store.pushError(`删除失败「${row.filename}」：${friendlyError(res.code, res.message)}`)
  }
}

async function batchDelete() {
  const rows = selection.value
  if (!rows.length) return
  const ok = await confirmDialog(`确认删除所选 ${rows.length} 个文档？删除后不可恢复。`, '批量删除')
  if (!ok) return
  const res = await api('/api/documents/batch-delete', {
    method: 'POST',
    silent: true,
    body: JSON.stringify({ document_ids: rows.map((r) => r.document_id) }),
  })
  if (res.code === 0) {
    const deleted = (res.data.deleted || []).length
    const failed = (res.data.failed || []).length
    if (deleted && !failed) store.pushInfo(`已删除 ${deleted} 个文档`)
    else if (deleted) store.pushError(`已删除 ${deleted} 个，${failed} 个未能删除`)
    else store.pushError('所选文档未能删除，可能已被移除')
    selection.value = []
    refreshDocs()
  } else {
    store.pushError(`删除失败：${friendlyError(res.code, res.message)}`)
  }
}

async function copyText(text, tip = '已复制') {
  try {
    await navigator.clipboard.writeText(String(text || ''))
    store.pushInfo(tip)
  } catch {
    store.pushError('复制失败，请手动选择')
  }
}

/* ---------------- 展示辅助 ---------------- */
const KIND_STYLE = {
  pdf: { label: 'PDF', bg: 'rgba(229,72,77,.12)', fg: '#e5484d' },
  doc: { label: 'DOC', bg: 'rgba(46,107,255,.12)', fg: '#2e6bff' },
  sheet: { label: 'XLS', bg: 'rgba(29,154,78,.12)', fg: '#1d9a4e' },
  slide: { label: 'PPT', bg: 'rgba(245,99,47,.12)', fg: '#f5632f' },
  md: { label: 'MD', bg: 'rgba(111,75,242,.12)', fg: '#6f4bf2' },
  code: { label: 'CODE', bg: 'rgba(120,120,128,.14)', fg: '#63636a' },
  config: { label: 'CFG', bg: 'rgba(178,106,0,.12)', fg: '#b26a00' },
  image: { label: 'IMG', bg: 'rgba(10,108,255,.12)', fg: '#0a6cff' },
  text: { label: 'TXT', bg: 'rgba(120,120,128,.12)', fg: '#63636a' },
}
function kindStyle(kind) {
  return KIND_STYLE[kind] || KIND_STYLE.text
}
</script>

<template>
  <div class="page-scroll">
    <div class="page">
      <PageHeader
        title="文档管理"
        :subtitle="`${currentKb ? currentKb.name : store.kbId} · 支持 ${SUPPORTED_TYPES}（单个 ≤ 50MB）`"
      >
        <template #actions>
          <el-select
            v-model="store.kbId"
            class="kb-select"
            placeholder="知识库"
            :disabled="uploading"
            @change="onKbChange"
          >
            <el-option v-for="k in kbs" :key="k.kb_id" :label="k.name" :value="k.kb_id" />
          </el-select>
          <el-button @click="refreshDocs">
            <el-icon style="margin-right: 6px"><Refresh /></el-icon>刷新
          </el-button>
          <el-upload
            v-if="!readOnly"
            :auto-upload="false"
            :show-file-list="false"
            :on-change="enqueue"
            :accept="SUPPORTED_ACCEPT"
            multiple
          >
            <el-button type="primary">
              <el-icon style="margin-right: 6px"><UploadFilled /></el-icon>上传文档
            </el-button>
          </el-upload>
        </template>
      </PageHeader>

      <!-- 指标 -->
      <div class="stat-row">
        <StatTile label="文档总数" :value="total" hint="当前知识库" :icon="Files" tone="accent" />
        <StatTile label="内容片段" :value="stats.chunks" hint="可检索的 chunk 总数" :icon="Document" />
        <StatTile label="最近入库" :value="stats.latest ? formatRelative(stats.latest * 1000) : '—'" hint="按文档创建时间" :icon="Clock" />
        <StatTile label="知识库" :value="kbs.length" :hint="currentKb && currentKb.scope === 'shared' ? '当前库为共享库' : '可切换的库总数'" :icon="FolderOpened" />
      </div>

      <div v-if="readOnly" class="readonly-hint">
        <el-icon><Lock /></el-icon>
        <span>共享知识库仅管理员可上传/删除文档；可切换到自己的私有知识库后再操作。</span>
      </div>

      <div v-if="listError" class="inline-error" role="alert">
        <el-icon><WarningFilled /></el-icon>
        <span>文档列表加载失败：{{ listError }}</span>
        <el-button size="small" text type="primary" @click="refreshDocs">重试</el-button>
      </div>

      <!-- 拖拽上传区 -->
      <div v-if="!readOnly" class="dropzone-wrap">
        <el-upload
          class="dropzone"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :on-change="enqueue"
          :accept="SUPPORTED_ACCEPT"
        >
          <el-icon class="dropzone-icon"><UploadFilled /></el-icon>
          <div class="dropzone-title">把文件拖到这里，或点击选择</div>
          <div class="dropzone-sub">多个文件会按顺序入库；重复内容会自动跳过</div>
        </el-upload>
      </div>

      <!-- 上传进度 -->
      <div v-if="progress || queue.length" class="upload-panel">
        <div class="upload-row">
          <el-icon class="upload-spin"><RefreshRight /></el-icon>
          <span class="upload-name truncate">{{ progress ? progress.filename : (queue[0] && queue[0].file ? queue[0].file.name : '') }}</span>
          <span class="upload-phase">
            {{ progress && progress.phase === 'ingest' ? '入库处理中…' : '上传中…' }}
            <template v-if="queue.length">（队列剩余 {{ queue.length }}）</template>
          </span>
          <el-progress
            class="upload-progress"
            :percentage="progress && progress.percent !== null ? progress.percent : 0"
            :indeterminate="!progress || progress.percent === null"
            :duration="2"
            :stroke-width="6"
            :show-text="false"
          />
          <button type="button" class="tool" @click="cancelUpload">取消</button>
        </div>
      </div>

      <!-- 工具栏 -->
      <div class="toolbar">
        <el-input
          v-model="query"
          class="search"
          placeholder="搜索文件名…"
          aria-label="搜索文件名"
          clearable
          :prefix-icon="Search"
          @input="onSearch"
        />
        <el-select v-model="kindFilter" class="filter" placeholder="类型" aria-label="按类型筛选" @change="onFilterChange">
          <el-option label="全部类型" value="all" />
          <el-option v-for="k in kindOptions" :key="k.value" :label="k.label" :value="k.value" />
        </el-select>
        <el-select v-model="sortBy" class="filter" placeholder="排序" aria-label="排序方式" @change="onFilterChange">
          <el-option label="最新优先" value="recent" />
          <el-option label="名称 A→Z" value="name" />
          <el-option label="片段数最多" value="chunks" />
        </el-select>
        <span class="spacer" />
        <el-button v-if="selection.length && !readOnly" type="danger" plain @click="batchDelete">
          <el-icon style="margin-right: 5px"><Delete /></el-icon>删除所选（{{ selection.length }}）
        </el-button>
      </div>

      <!-- 列表 -->
      <div class="card card--flush table-card">
        <el-table
          v-loading="loading"
          :data="files"
          row-key="document_id"
          size="default"
          @selection-change="(rows) => (selection = rows)"
        >
          <el-table-column v-if="!readOnly" type="selection" width="44" />
          <el-table-column label="文件" :min-width="isMobile ? 180 : 280">
            <template #default="{ row }">
              <div class="file-cell">
                <span class="kind-badge" :style="{ background: kindStyle(row.kind).bg, color: kindStyle(row.kind).fg }">
                  {{ kindStyle(row.kind).label }}
                </span>
                <div class="file-text">
                  <span class="file-name truncate" :title="row.filename">{{ row.filename }}</span>
                  <span class="file-sub">
                    <template v-if="row.version > 1">第 {{ row.version }} 版 · </template>
                    {{ row.updatedRelative ? `更新于 ${row.updatedRelative}` : `入库于 ${row.createdRelative}` }}
                  </span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column v-if="!isMobile" label="片段" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ row.chunk_count }}</span></template>
          </el-table-column>
          <el-table-column v-if="!isMobile" label="入库时间" width="150">
            <template #default="{ row }"><span class="muted">{{ row.created }}</span></template>
          </el-table-column>
          <el-table-column label="操作" :width="isMobile ? 148 : 190" align="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-tooltip content="查看详情" placement="top">
                  <button type="button" class="btn-icon" aria-label="查看详情" @click="openDetail(row)">
                    <el-icon><View /></el-icon>
                  </button>
                </el-tooltip>
                <template v-if="!readOnly">
                  <el-tooltip content="用同名文件更新（保留旧版本直到新版本入库成功）" placement="top">
                    <button type="button" class="btn-icon" aria-label="更新文档" @click="askReplace(row, 'upsert')">
                      <el-icon><Refresh /></el-icon>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="强制重建索引（同一文件重新解析与向量化）" placement="top">
                    <button type="button" class="btn-icon" aria-label="重建索引" @click="forceRebuild(row)">
                      <el-icon><RefreshRight /></el-icon>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="删除" placement="top">
                    <button type="button" class="btn-icon btn-icon--danger" aria-label="删除文档" @click="removeDoc(row)">
                      <el-icon><Delete /></el-icon>
                    </button>
                  </el-tooltip>
                </template>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <EmptyState
          v-if="!files.length && !loading"
          :icon="query || kindFilter !== 'all' ? Search : Document"
          :title="query || kindFilter !== 'all' ? '没有匹配的文档' : '这个知识库还没有文档'"
          :description="query || kindFilter !== 'all'
            ? '换个关键词，或把类型筛选切回「全部类型」。'
            : '拖拽文件到上方区域，或点击「上传文档」开始构建知识库。'"
          compact
        >
          <template v-if="!query && kindFilter === 'all' && !readOnly" #default>
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              :on-change="enqueue"
              :accept="SUPPORTED_ACCEPT"
              multiple
            >
              <el-button type="primary">选择文件</el-button>
            </el-upload>
          </template>
        </EmptyState>

        <div v-if="total > 0" class="table-foot">
          <span class="muted">
            共 {{ total }} 条<template v-if="total > pageSize"> · 第 {{ page }} / {{ Math.ceil(total / pageSize) }} 页</template>
          </span>
          <div class="foot-controls">
            <el-select
              :model-value="pageSize"
              class="page-size"
              size="small"
              aria-label="每页条数"
              @change="onPageSizeChange"
            >
              <el-option v-for="n in pageSizes" :key="n" :label="`${n} 条/页`" :value="n" />
            </el-select>
            <el-pagination
              v-if="total > pageSize"
              v-model:current-page="page"
              :page-size="pageSize"
              :total="total"
              layout="prev, pager, next"
              background
              size="small"
              @current-change="refreshDocs()"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 行级上传用的隐藏 input（upsert / force 共用） -->
    <input ref="fileInput" type="file" class="hidden-input" :accept="SUPPORTED_ACCEPT" @change="pendingAction && pendingAction.mode === 'force' ? pickForceFile($event) : onReplacePicked($event)" @cancel="pendingAction = null" />

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailOpen" title="文档详情" size="420px">
      <div v-if="detail" v-loading="detailLoading" class="detail">
        <div class="detail-head">
          <span class="kind-badge" :style="{ background: kindStyle(detail.kind).bg, color: kindStyle(detail.kind).fg }">
            {{ kindStyle(detail.kind).label }}
          </span>
          <h3 class="detail-name">{{ detail.filename }}</h3>
        </div>
        <dl class="detail-list">
          <div><dt>文档 ID</dt><dd class="mono">{{ detail.document_id }}
            <button type="button" class="tool" @click="copyText(detail.document_id, '文档 ID 已复制')">
              <el-icon><CopyDocument /></el-icon>
            </button>
          </dd></div>
          <div><dt>所属知识库</dt><dd>{{ detail.kb_id }}</dd></div>
          <div><dt>标题</dt><dd>{{ detail.title || '—' }}</dd></div>
          <div><dt>片段数</dt><dd>{{ detail.chunk_count }}</dd></div>
          <div><dt>版本</dt><dd>v{{ detail.version || 1 }}</dd></div>
          <div><dt>入库时间</dt><dd>{{ formatDateTime(detail.created_at) }}</dd></div>
          <div><dt>最近更新</dt><dd>{{ detail.updated_at ? formatDateTime(detail.updated_at) : '—' }}</dd></div>
          <div><dt>归档原件</dt><dd class="mono break">{{ detail.archive_path || '未归档（archive 关闭）' }}</dd></div>
        </dl>
        <p class="muted">
          「更新」会在新版本成功入库后才删除旧版本，更新期间查询不中断；「重建」跳过幂等校验，强制重新解析与向量化。
        </p>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.inline-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
}
.inline-error .el-button { margin-left: auto; color: var(--danger); }
.stat-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.kb-select { width: 190px; }

.readonly-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--warning-soft);
  color: var(--warning);
  font-size: 12.5px;
}

/* 拖拽区 */
.dropzone-wrap :deep(.el-upload) { display: block; }
.dropzone :deep(.el-upload-dragger) {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 22px 16px;
  border: 1.5px dashed var(--hairline-strong);
  border-radius: var(--radius-lg);
  background: var(--fill-1);
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.dropzone :deep(.el-upload-dragger:hover) { border-color: var(--accent); background: var(--accent-soft); }
.dropzone-icon { font-size: 26px; color: var(--accent); margin-bottom: 4px; }
.dropzone-title { font-size: 13px; font-weight: 600; color: var(--text); }
.dropzone-sub { font-size: 11.5px; color: var(--text-tertiary); }

/* 上传进度 */
.upload-panel {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
  background: var(--surface);
  box-shadow: var(--shadow-1);
}
.upload-row { display: flex; align-items: center; gap: 10px; }
.upload-spin { color: var(--accent); animation: spin 1.2s linear infinite; }
.upload-name { font-size: 12.5px; font-weight: 600; max-width: 220px; }
.upload-phase { font-size: 11.5px; color: var(--text-tertiary); white-space: nowrap; }
.upload-progress { flex: 1; min-width: 80px; }

/* 工具栏 */
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.search { width: 240px; }
.filter { width: 132px; }
.spacer { flex: 1; }

.table-card { padding: 4px 16px 8px; }
.file-cell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.kind-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 40px;
  height: 22px;
  padding: 0 6px;
  border-radius: 6px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.03em;
  flex: none;
}
.file-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.35; }
.file-name { font-size: 13px; font-weight: 550; }
.file-sub { font-size: 11px; color: var(--text-tertiary); }
.num { font-variant-numeric: tabular-nums; }
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 2px; }
.btn-icon--danger:hover { color: var(--danger); background: var(--danger-soft); }
.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 2px 4px;
  border-top: 1px solid var(--hairline);
  flex-wrap: wrap;
}
.foot-controls { display: flex; align-items: center; gap: 10px; }
.page-size { width: 108px; }
.hidden-input { display: none; }
.limit-hint { margin: 6px 2px 0; }

/* 抽屉 */
.detail-head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.detail-name { margin: 0; font-size: 15px; word-break: break-all; }
.detail-list { margin: 0 0 14px; display: flex; flex-direction: column; gap: 1px; }
.detail-list > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 0;
  border-bottom: 1px solid var(--hairline);
}
.detail-list dt { font-size: 12px; color: var(--text-tertiary); flex: none; }
.detail-list dd { margin: 0; font-size: 12.5px; text-align: right; word-break: break-all; }
.break { word-break: break-all; }
.tool {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 11.5px;
  padding: 0 4px;
}
.tool:hover { color: var(--accent); }

@media (max-width: 1100px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .search { width: 100%; }
  .filter { flex: 1; }
  .kb-select { width: 100%; }
  .upload-name { max-width: 120px; }
}
</style>
