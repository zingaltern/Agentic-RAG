<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Delete, Document, Lock, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { api } from '../api'
import { getBaseUrl } from '../api/base'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const SUPPORTED_ACCEPT = '.pdf,.docx,.xlsx,.pptx,.md,.txt,.html,.htm,.eml,.msg'
const SUPPORTED_TYPES = 'PDF / Word(.docx) / Excel / PPT / Markdown / 文本 / 网页 / 邮件'
const MAX_FILE_SIZE = 50 * 1024 * 1024   // 与服务端 max_file_size_mb: 50 对齐，上传前预检
const UPLOAD_TIMEOUT_MS = 2 * 60 * 1000  // XHR 上传整体超时，避免永久挂起
const kbs = ref([])
const kbId = ref('default')
const files = ref([])
const uploading = ref(false)
const progress = ref(null)     // { phase: 'upload'|'ingest', percent: 0-100 | null(不确定) }
const timers = []
const uploadQueue = []         // 上传队列（串行执行：单进度条 + 不互相覆盖）
const selection = ref([])      // el-table 勾选行（批量删除）
const tableRef = ref(null)

onMounted(() => { loadKbs(); refreshDocs() })

const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'
const currentKb = ref(null)
const readOnly = computed(() => {
  const kb = kbs.value.find((k) => k.kb_id === kbId.value)
  return Boolean(kb && kb.scope === 'shared' && !isAdmin())
})
watch(kbs, () => {
  const kb = kbs.value.find((k) => k.kb_id === kbId.value)
  currentKb.value = kb || null
})
onUnmounted(() => { timers.forEach((t) => clearInterval(t)); timers.length = 0 })

async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) kbs.value = res.data.kbs || []
}

async function refreshDocs() {
  selection.value = []
  const params = new URLSearchParams({ page: '1', size: '100' })
  if (kbId.value) params.set('kb_id', kbId.value)
  const res = await api(`/api/documents?${params.toString()}`)
  if (res.code === 0) {
    const items = res.data.items || []
    files.value = items.map((d) => ({
      ...d,
      created: new Date(d.created_at * 1000).toLocaleString(),
    }))
  }
}

function onPick(uploadFile) {
  const file = uploadFile.raw
  if (!file) return
  // 客户端预检 50MB（与后端 max_file_size_mb 对齐），超限不发请求
  if (file.size > MAX_FILE_SIZE) {
    store.pushError(`「${file.name}」超过 50MB 限制，未上传`)
    return
  }
  uploadQueue.push(file)
  drainQueue()
}

/** 串行消费上传队列：单个进度条、pollJob 不互相覆盖；全部完成后 loading 状态正确回落 */
async function drainQueue() {
  if (uploading.value) return
  const next = uploadQueue.shift()
  if (!next) return
  await uploadOne(next)
  drainQueue()
}

function uploadWithProgress(form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', getBaseUrl() + '/api/documents/upload')
    xhr.timeout = UPLOAD_TIMEOUT_MS
    const token = localStorage.getItem('auth_token') || ''
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('X-Trace-Id', (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      try { resolve(JSON.parse(xhr.responseText)) } catch { reject(new Error('响应解析失败')) }
    }
    xhr.onerror = () => reject(new Error('网络上传失败'))
    xhr.onabort = () => reject(new Error('上传已中止'))
    xhr.ontimeout = () => reject(new Error('上传超时'))
    xhr.send(form)
  })
}

async function uploadOne(file) {
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', kbId.value)
  progress.value = { phase: 'upload', percent: 0, filename: file.name }
  try {
    const res = await uploadWithProgress(form, (p) => {
      progress.value = { phase: 'upload', percent: p, filename: file.name }
    })
    if (res.code === 0) {
      if (res.data.duplicate) {
        store.pushInfo(`「${file.name}」与知识库中已有文档内容相同，已跳过`)
        progress.value = null
      } else {
        store.pushInfo(`已提交上传「${file.name}」，正在处理…`)
        progress.value = { phase: 'ingest', percent: null, filename: file.name }
        pollJob(res.data.job_id, file.name)
      }
    } else {
      store.pushError(`上传失败「${file.name}」：${res.message || '请检查文件后重试'}`)
      progress.value = null
    }
  } catch (e) {
    const tip = e.message === '上传超时' ? '上传超时，请重试' : e.message || '请重试'
    store.pushError(`上传失败「${file.name}」：${tip}`)
    progress.value = null
  } finally {
    uploading.value = false
  }
}

function pollJob(jobId, filename) {
  let attempts = 0
  const MAX_ATTEMPTS = 240   // 500ms × 240 ≈ 2 分钟，超时视为入库滞留在途
  const timer = setInterval(async () => {
    attempts += 1
    const res = await api(`/api/jobs/${jobId}`)
    if (res.code !== 0) { clearInterval(timer); progress.value = null; return }
    const s = res.data.status
    if (s === 'success' || s === 'failed') {
      clearInterval(timer)
      progress.value = null
      if (s === 'success') {
        store.pushInfo(`「${filename}」已加入知识库，共 ${res.data.chunk_count} 个内容片段`)
      } else {
        store.pushError(`处理失败「${filename}」：请确认文件格式受支持且内容可解析`)
      }
      refreshDocs()
    } else if (attempts >= MAX_ATTEMPTS) {
      clearInterval(timer)
      progress.value = null
      store.pushError(`入库超时：${filename}（请稍后在文档列表确认）`)
    }
  }, 500)
  timers.push(timer)
}

async function removeDoc(row) {
  const res = await api(`/api/documents/${row.document_id}`, { method: 'DELETE', silent: true })
  if (res.code === 0) {
    store.pushInfo(`已删除「${row.filename}」`)
    refreshDocs()
  } else {
    store.pushError(`删除失败「${row.filename}」：${res.message || '请重试'}`)
  }
}

async function batchDeleteDocs() {
  const rows = selection.value
  const ids = rows.map((row) => row.document_id)
  if (!ids.length) return
  const res = await api('/api/documents/batch-delete', {
    method: 'POST',
    silent: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_ids: ids }),
  })
  if (res.code === 0) {
    const failed = res.data.failed || []
    const deletedCount = (res.data.deleted || []).length
    if (deletedCount > 0 && !failed.length) {
      store.pushInfo(`已删除 ${deletedCount} 个文档`)
    } else if (deletedCount > 0) {
      store.pushError(`已删除 ${deletedCount} 个文档，${failed.length} 个未能删除，可刷新后查看`)
    } else {
      store.pushError('所选文档未能删除，可能已被移除，可刷新后查看')
    }
    tableRef.value && tableRef.value.clearSelection()
    refreshDocs()
  } else {
    store.pushError(`删除失败：${res.message || '请重试'}`)
  }
}
</script>

<template>
  <div>
    <div class="card">
      <h2 class="card-title"><span class="title-icon"><el-icon><Document /></el-icon></span>文档管理</h2>
      <div v-if="readOnly" class="perm-hint">
        <el-icon><Lock /></el-icon>
        <span>共享知识库仅管理员可上传文档，可切换到你的私有知识库后再上传</span>
      </div>
      <div class="row docs-toolbar">
        <div class="kb-block">
          <span>目标知识库：</span>
          <el-select v-model="kbId" style="width: 240px" @change="refreshDocs">
            <el-option v-for="k in kbs" :key="k.kb_id" :label="k.name" :value="k.kb_id" />
          </el-select>
          <div class="muted support-hint">
            支持：{{ SUPPORTED_TYPES }}（单个 ≤ 50MB）
          </div>
        </div>
        <div class="actions">
          <el-popconfirm
            v-if="!readOnly && selection.length"
            :title="`确认删除所选 ${selection.length} 个文档？删除后不可恢复`"
            confirm-button-text="删除"
            cancel-button-text="取消"
            width="260"
            @confirm="batchDeleteDocs"
          >
            <template #reference>
              <el-button type="danger">
                <el-icon style="margin-right: 5px"><Delete /></el-icon>删除所选 ({{ selection.length }})
              </el-button>
            </template>
          </el-popconfirm>
          <el-upload v-if="!readOnly"
            :auto-upload="false"
            :show-file-list="false"
            :on-change="onPick"
            :accept="SUPPORTED_ACCEPT"
            multiple
          >
            <el-button type="primary" :loading="uploading">
              <el-icon style="margin-right: 5px"><UploadFilled /></el-icon>选择文件上传
            </el-button>
          </el-upload>
          <el-button @click="refreshDocs"><el-icon style="margin-right: 5px"><Refresh /></el-icon>刷新列表</el-button>
        </div>
      </div>
      <div v-if="progress" class="progress-wrap">
        <el-progress
          :percentage="progress.percent === null ? 0 : progress.percent"
          :indeterminate="progress.percent === null"
          :duration="3"
          :stroke-width="10"
          :text-inside="true"
        >
          <span>{{ progress.filename ? progress.filename + '：' : '' }}{{ progress.phase === 'upload' ? '上传中…' : '入库处理中…' }}</span>
        </el-progress>
      </div>
    </div>

    <div class="card">
      <el-table ref="tableRef" :data="files" size="small" border row-key="document_id" @selection-change="(rows) => (selection = rows)">
        <el-table-column v-if="!readOnly" type="selection" width="40" />
        <el-table-column prop="filename" label="文件名" min-width="180" />
        <el-table-column prop="chunk_count" label="块数" width="70" />
        <el-table-column prop="created" label="入库时间" width="170" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-popconfirm
              v-if="!readOnly"
              :title="`确认删除「${row.filename}」？删除后不可恢复`"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="removeDoc(row)"
            >
              <template #reference>
                <el-button type="danger" size="small">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-popconfirm>
            <el-button v-else type="danger" size="small" disabled>
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!files.length" class="muted" style="padding: 14px 0">
        <template v-if="readOnly">
          该知识库暂无文档——共享库需由管理员上传文档
        </template>
        <template v-else>
          暂无文档——点击「选择文件上传」将文档加入知识库（支持 {{ SUPPORTED_TYPES }}；老版 .doc 请先另存为 .docx）
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.progress-wrap { margin-top: 14px; max-width: 480px; }
.docs-toolbar { display: flex; align-items: flex-start; gap: 24px; flex-wrap: wrap; }
.kb-block { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.kb-block > span { line-height: 32px; }
.actions { display: flex; align-items: center; gap: 10px; margin-left: auto; align-self: flex-end; }
.support-hint { font-size: 12px; margin-top: 4px; }
.perm-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  padding: 7px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 6px;
  width: fit-content;
}
</style>
