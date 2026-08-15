<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Delete, Document, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { api } from '../api'
import { getBaseUrl } from '../api/base'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const SUPPORTED_ACCEPT = '.pdf,.docx,.xlsx,.pptx,.md,.txt'
const SUPPORTED_TYPES = 'PDF / Word(.docx) / Excel / PPT / Markdown / 文本'
const kbs = ref([])
const kbId = ref('default')
const files = ref([])
const uploading = ref(false)
const progress = ref(null)     // { phase: 'upload'|'ingest', percent: 0-100 | null(不确定) }
const timers = []

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
  uploading.value = true
  uploadOne(file)
}

function uploadWithProgress(form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', getBaseUrl() + '/api/documents/upload')
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
    xhr.send(form)
  })
}

async function uploadOne(file) {
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', kbId.value)
  progress.value = { phase: 'upload', percent: 0 }
  try {
    const res = await uploadWithProgress(form, (p) => {
      progress.value = { phase: 'upload', percent: p }
    })
    if (res.code === 0) {
      if (res.data.duplicate) {
        store.pushInfo(`已存在相同文档：${file.name}`)
        progress.value = null
      } else {
        store.pushInfo(`上传已提交：${file.name}`)
        progress.value = { phase: 'ingest', percent: null }
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
  const timer = setInterval(async () => {
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
          <el-option v-for="k in kbs" :key="k.kb_id" :label="`${k.name}（${k.kb_id}）`" :value="k.kb_id" />
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
        <el-tag v-if="readOnly" type="warning" size="small">只读（共享库，仅管理员可写）</el-tag>
        <span class="muted" style="margin-left: 10px; font-size: 12px">
          支持：{{ SUPPORTED_TYPES }}（单个 ≤ 20MB）
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
          <span>{{ progress.phase === 'upload' ? '上传中…' : '入库处理中…' }}</span>
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
            <el-tooltip content="删除文档及向量块" placement="top">
              <el-button type="danger" size="small" @click="removeDoc(row.document_id)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </el-tooltip>
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
