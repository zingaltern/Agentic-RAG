<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Delete, Document, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { api } from '../api'
import { getBaseUrl } from '../api/base'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const SUPPORTED_ACCEPT = '.pdf,.docx,.xlsx,.pptx,.md,.txt'
const SUPPORTED_TYPES = 'PDF / Word(.docx) / Excel / PPT / Markdown / 文本'
const MAX_FILE_SIZE = 50 * 1024 * 1024   // 与服务端 max_file_size_mb: 50 对齐，上传前预检
const UPLOAD_TIMEOUT_MS = 2 * 60 * 1000  // XHR 上传整体超时，避免永久挂起
const kbs = ref([])
const kbId = ref('default')
const files = ref([])
const uploading = ref(false)
const progress = ref(null)     // { phase: 'upload'|'ingest', percent: 0-100 | null(不确定) }
const timers = []
const uploadQueue = []         // 上传队列（串行执行：单进度条 + 不互相覆盖）

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
    store.pushError(`${file.name} 超过 50MB 限制，未上传`)
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
        store.pushInfo(`已存在相同文档：${file.name}`)
        progress.value = null
      } else {
        store.pushInfo(`上传已提交：${file.name}`)
        progress.value = { phase: 'ingest', percent: null, filename: file.name }
        pollJob(res.data.job_id, file.name)
      }
    } else {
      store.pushError(res.message || `上传失败：${file.name}`)
      progress.value = null
    }
  } catch (e) {
    store.pushError(`上传失败：${e.message}`)
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
      store.pushInfo(
        s === 'success'
          ? `入库成功：${filename}（${res.data.chunk_count} 个文本块）`
          : `入库失败：${filename}`,
      )
      refreshDocs()
    } else if (attempts >= MAX_ATTEMPTS) {
      clearInterval(timer)
      progress.value = null
      store.pushError(`入库超时：${filename}（请稍后在文档列表确认）`)
    }
  }, 500)
  timers.push(timer)
}

async function removeDoc(documentId) {
  const res = await api(`/api/documents/${documentId}`, { method: 'DELETE' })
  if (res.code === 0) {
    store.pushInfo('已删除（级联清向量块）')
    refreshDocs()
  }
}
</script>

<template>
  <div>
    <div class="card">
      <h2 class="card-title"><span class="title-icon"><el-icon><Document /></el-icon></span>文档管理</h2>
      <div class="row">
        <span>目标知识库：</span>
        <el-select v-model="kbId" style="width: 240px">
          <el-option v-for="k in kbs" :key="k.kb_id" :label="k.name" :value="k.kb_id" />
        </el-select>
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
        <span class="muted" style="margin-left: 10px; font-size: 12px">
          支持：{{ SUPPORTED_TYPES }}（单个 ≤ 50MB）
        </span>
        <el-button @click="refreshDocs"><el-icon style="margin-right: 5px"><Refresh /></el-icon>刷新列表</el-button>
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
      <el-table :data="files" size="small" border>
        <el-table-column prop="filename" label="文件名" min-width="180" />
        <el-table-column prop="chunk_count" label="块数" width="70" />
        <el-table-column prop="created" label="入库时间" width="170" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-popconfirm
              v-if="!readOnly"
              title="删除文档将级联清空其向量块，不可恢复。确认删除？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="removeDoc(row.document_id)"
            >
              <template #reference>
                <el-tooltip content="删除文档及向量块" placement="top">
                  <el-button type="danger" size="small">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </el-tooltip>
              </template>
            </el-popconfirm>
            <el-button v-else type="danger" size="small" disabled>
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!files.length" class="muted" style="padding: 14px 0">
        暂无文档——点击「选择文件上传」将文档加入知识库（支持 {{ SUPPORTED_TYPES }}；老版 .doc 请先另存为 .docx）
      </div>
    </div>
  </div>
</template>

<style scoped>
.progress-wrap { margin-top: 14px; max-width: 480px; }
</style>
