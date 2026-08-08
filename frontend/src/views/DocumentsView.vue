<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const kbId = ref('default')
const files = ref([])
const uploading = ref(false)
const pollTimer = ref(null)

onMounted(() => { loadKbs(); refreshDocs() })

async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) kbs.value = res.data.kbs || []
}

async function refreshDocs() {
  const res = await api('/api/documents?page=1&size=100')
  if (res.code === 0) {
    const items = res.data.items || []
    files.value = items.map((d) => ({
      ...d,
      created: new Date(d.created_at * 1000).toLocaleString(),
    }))
  }
}

function onPick(event) {
  const list = Array.from(event.target.files || [])
  if (!list.length) return
  uploading.value = true
  for (const file of list) uploadOne(file)
}

async function uploadOne(file) {
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', kbId.value)
  const res = await api('/api/documents/upload', { method: 'POST', body: form })
  if (res.code === 0) {
    if (res.data.duplicate) {
      store.pushError(`已存在（duplicate）：${file.name} → ${res.data.document_id}`)
    } else {
      store.pushError(`上传已提交：${file.name}（job=${res.data.job_id}，kb=${res.data.kb_id}）`)
      pollJob(res.data.job_id)
    }
  }
}

function pollJob(jobId) {
  // 500ms 轮询（§7 旧页同款语义）
  const timer = setInterval(async () => {
    const res = await api(`/api/jobs/${jobId}`)
    if (res.code !== 0) { clearInterval(timer); return }
    const s = res.data.status
    if (s === 'success' || s === 'failed') {
      clearInterval(timer)
      store.pushError(
        s === 'success'
          ? `入库成功：${res.data.filename}（chunk=${res.data.chunk_count}）`
          : `入库失败：${res.data.filename}（error_code=${res.data.error_code}）`,
      )
      refreshDocs()
    }
  }, 500)
}

async function removeDoc(documentId) {
  const res = await api(`/api/documents/${documentId}`, { method: 'DELETE' })
  if (res.code === 0) {
    store.pushError('已删除（级联清向量块）')
    refreshDocs()
  }
}
</script>

<template>
  <div>
    <div class="card">
      <h2>② 文档管理</h2>
      <div class="row">
        <span>目标知识库：</span>
        <el-select v-model="kbId" style="width: 220px">
          <el-option v-for="k in kbs" :key="k.kb_id" :label="`${k.name}（${k.kb_id}）`" :value="k.kb_id" />
        </el-select>
        <el-upload :auto-upload="false" :show-file-list="false" :on-change="onPick" multiple>
          <el-button type="primary" :loading="uploading">选择文件上传</el-button>
        </el-upload>
        <el-button @click="refreshDocs">刷新列表</el-button>
      </div>
    </div>

    <div class="card">
      <el-table :data="files" size="small" border>
        <el-table-column prop="filename" label="文件名" min-width="180" />
        <el-table-column prop="document_id" label="document_id" min-width="130" />
        <el-table-column prop="chunk_count" label="块数" width="70" />
        <el-table-column prop="created" label="入库时间" width="170" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button type="danger" size="small" @click="removeDoc(row.document_id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!files.length" class="muted" style="padding: 10px 0">暂无文档</div>
    </div>
  </div>
</template>
