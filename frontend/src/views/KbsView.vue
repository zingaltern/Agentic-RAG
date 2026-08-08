<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const dialog = ref(false)
const form = ref({ name: '', description: '' })
const saving = ref(false)

onMounted(loadKbs)
async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) kbs.value = res.data.kbs || []
}

async function createKb() {
  if (!form.value.name.trim()) { store.pushError('请输入知识库名称'); return }
  saving.value = true
  const res = await api('/api/kbs', {
    method: 'POST',
    body: JSON.stringify(form.value),
  })
  saving.value = false
  if (res.code === 0) {
    store.pushError(`已创建：${res.data.kb_id}`)
    dialog.value = false
    form.value = { name: '', description: '' }
    loadKbs()
  }
}

async function removeKb(kbId) {
  const res = await api(`/api/kbs/${kbId}`, { method: 'DELETE' })
  if (res.code === 0) {
    store.pushError(`已删除知识库：${kbId}（集合级联清空）`)
    loadKbs()
  }
}
</script>

<template>
  <div>
    <div class="card">
      <div class="row">
        <h2 style="flex: 1">③ 知识库管理</h2>
        <el-button type="primary" @click="dialog = true">新建知识库</el-button>
      </div>
      <el-table :data="kbs" size="small" border>
        <el-table-column prop="kb_id" label="kb_id" min-width="120" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="description" label="描述" min-width="180" />
        <el-table-column prop="collection" label="collection" width="120" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-tooltip v-if="row.kb_id === 'default'" content="默认库不可删除" placement="top">
              <span><el-button type="danger" size="small" disabled>删除</el-button></span>
            </el-tooltip>
            <el-button v-else type="danger" size="small" @click="removeKb(row.kb_id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" title="新建知识库" width="420px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：HR Policies / 报销制度" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createKb">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
