<script setup>
import { onMounted, ref } from 'vue'
import { Delete, FolderOpened, FolderAdd } from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const kbs = ref([])
const dialog = ref(false)
const form = ref({ name: '', description: '', scope: 'shared' })
const saving = ref(false)
const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'
const isPersonalMode = () => store.currentUser && store.currentUser.user_id === 'local'

onMounted(loadKbs)
async function loadKbs() {
  const res = await api('/api/kbs')
  if (res.code === 0) kbs.value = res.data.kbs || []
}

async function createKb() {
  if (!form.value.name.trim()) { store.pushError('请输入知识库名称'); return }
  saving.value = true
  try {
    // 普通用户无权建共享库（服务端强制 private，这里前端对齐语义，避免发送误导性的 shared）
    const payload = { ...form.value }
    if (!(isAdmin() && !isPersonalMode())) payload.scope = 'private'
    const res = await api('/api/kbs', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (res.code === 0) {
      store.pushInfo(`已创建：${res.data.kb_id}`)
      dialog.value = false
      form.value = { name: '', description: '', scope: 'shared' }
      loadKbs()
    }
  } finally {
    saving.value = false
  }
}

function readOnlyKb(row) {
  return row.scope === 'shared' && !isAdmin()
}

async function removeKb(kbId) {
  const res = await api(`/api/kbs/${kbId}`, { method: 'DELETE' })
  if (res.code === 0) {
    store.pushInfo(`已删除知识库：${kbId}（集合级联清空）`)
    loadKbs()
  }
}
</script>

<template>
  <div>
    <div class="card">
      <div class="row">
        <h2 class="card-title" style="flex: 1; margin: 0">
          <span class="title-icon"><el-icon><FolderOpened /></el-icon></span>知识库管理
        </h2>
        <el-button type="primary" @click="dialog = true">
          <el-icon style="margin-right: 5px"><FolderAdd /></el-icon>新建知识库
        </el-button>
      </div>
      <el-table :data="kbs" size="small" border>
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="description" label="描述" min-width="180" />
        <el-table-column v-if="!isPersonalMode()" label="归属" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.scope === 'shared'" type="info" size="small">共享</el-tag>
            <el-tag v-else type="warning" size="small">私有 · {{ row.owner }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-tooltip v-if="isAdmin() && row.kb_id === 'default'" content="默认库不可删除" placement="top">
              <span><el-button type="danger" size="small" disabled>删除</el-button></span>
            </el-tooltip>
            <el-popconfirm
              v-else-if="!readOnlyKb(row)"
              title="删除知识库将级联清空全部向量，不可恢复。确认删除？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="removeKb(row.kb_id)"
            >
              <template #reference>
                <el-button type="danger" size="small">
                  <el-icon style="margin-right: 4px"><Delete /></el-icon>删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div class="muted" style="margin-top: 10px">
        每个知识库对应一个向量集合（collection），由名称自动生成并分配集合名，无需手动配置。
      </div>
    </div>

    <el-dialog v-model="dialog" title="新建知识库" width="420px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：HR Policies / 报销制度" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item v-if="isAdmin() && !isPersonalMode()" label="归属">
          <el-select v-model="form.scope">
            <el-option label="共享" value="shared" />
            <el-option label="私有" value="private" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createKb">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
