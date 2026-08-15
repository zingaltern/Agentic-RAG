<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import { clearToken } from '../api/base'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const health = ref(null)
const plugins = ref({})
const externalUrl = ref('')
const users = ref([])
const userDialog = ref(false)
const userForm = ref({ username: '', name: '', password: '', role: 'user' })
const savingUser = ref(false)

const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'
const isPersonalMode = () => (store.currentUser && store.currentUser.user_id) === 'local'

let healthTimer = null
onMounted(() => {
  loadHealth(); loadPlugins(); loadExternal(); if (isAdmin()) loadUsers()
  healthTimer = setInterval(loadHealth, 10000)
})
onUnmounted(() => clearInterval(healthTimer))

async function loadHealth() {
  const res = await api('/api/health')
  if (res.code === 0) health.value = res.data
}
async function loadPlugins() {
  const res = await api('/api/plugins/status')
  if (res.code === 0) plugins.value = res.data.modules || {}
}
async function loadUsers() {
  const res = await api('/api/users')
  if (res.code === 0) users.value = res.data.users || []
}
function loadExternal() {
  // 桌面端以 userData 持久化为准（IPC 回读），Web 端读 localStorage——避免双存储不一致
  if (window.desktop && window.desktop.getExternalBase) {
    window.desktop.getExternalBase()
      .then((url) => { externalUrl.value = url || '' })
      .catch(() => {})
  } else {
    try { externalUrl.value = localStorage.getItem('external_base') || '' } catch { externalUrl.value = '' }
  }
}

function logout() {
  clearToken()
  store.logout()
  // 无需跳转：App.vue 依据 authenticated 自动切换回登录视图
}

function saveExternal() {
  // 先回读生效值（桌面端以 userData 持久化为准）再写入，保证设置页显示与实际生效一致
  loadExternal()
  const url = externalUrl.value.trim()
  externalUrl.value = url
  try { localStorage.setItem('external_base', url) } catch { /* ignore */ }
  if (window.desktop && window.desktop.setExternalBase) {
    window.desktop.setExternalBase(url)
    store.pushInfo('外部后端已保存：重启桌面应用后生效（届时不再自动拉起本地服务）')
  } else {
    store.pushInfo('外部后端已保存：Web 模式需后端开启 CORS 或同源部署')
  }
}

async function createUser() {
  if (!userForm.value.username.trim() || !userForm.value.password) {
    store.pushError('请输入用户名与初始密码'); return
  }
  savingUser.value = true
  const res = await api('/api/users', { method: 'POST', body: JSON.stringify(userForm.value) })
  savingUser.value = false
  if (res.code === 0) {
    store.pushInfo(`已创建用户：${userForm.value.username}`)
    userDialog.value = false
    userForm.value = { username: '', name: '', password: '', role: 'user' }
    loadUsers()
  }
}

async function resetPassword(userId) {
  const { value } = await ElMessageBox.prompt(
    '请输入新密码（至少 6 位）', '重置密码',
    { inputPattern: /^.{6,}$/, inputErrorMessage: '密码至少 6 位' },
  ).catch(() => ({ value: null }))
  if (!value) return    // 用户取消：不发起请求
  const res = await api(`/api/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: value }) })
  if (res.code === 0) store.pushInfo('密码已重置')
}
async function changeRole(userId, username, newRole) {
  const ok = await ElMessageBox.confirm(
    `确定将 ${username} 设为${newRole === 'admin' ? '管理员' : '普通用户'}？`,
    '变更角色', { type: 'warning' },
  ).catch(() => false)
  if (!ok) return
  const res = await api(`/api/users/${userId}/role`, { method: 'POST', body: JSON.stringify({ role: newRole }) })
  if (res.code === 0) { store.pushInfo('角色已更新'); loadUsers() }
}
async function removeUser(userId, username) {
  const ok = await ElMessageBox.confirm(`确定删除用户 ${username}？`, '删除用户', { type: 'warning' }).catch(() => false)
  if (!ok) return
  const res = await api(`/api/users/${userId}`, { method: 'DELETE' })
  if (res.code === 0) { store.pushInfo('用户已删除'); loadUsers() }
}

const importDialog = ref(false)
const importing = ref(false)
const importCreated = ref(0)
const importErrors = ref([])

function downloadTemplate() {
  const csv = '用户名,姓名,初始密码,角色\nzs0001,张三,zs123456,user\nls0002,李四,ls123456,admin\n'
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = '用户导入模板.csv'
  a.click()
  URL.revokeObjectURL(url)
}

function onImportPick(file) {
  const raw = file.raw || file
  const reader = new FileReader()
  reader.onload = async () => {
    importErrors.value = []
    importCreated.value = 0
    const form = new FormData()
    form.append('file', new Blob([String(reader.result || '')], { type: 'text/csv' }), 'users.csv')
    importing.value = true
    try {
      const res = await api('/api/users/import', { method: 'POST', body: form })
      if (res.code === 0) {
        importCreated.value = res.data.created || 0
        store.pushInfo(`批量导入成功：${importCreated.value} 个用户`)
        loadUsers()
      } else {
        importErrors.value = (res.data && res.data.errors) || [{ row: 0, message: res.message || '导入失败' }]
        store.pushError('批量导入失败：部分账号存在问题，未写入任何用户')
      }
    } finally {
      importing.value = false
    }
  }
  reader.readAsText(raw)
}

function checkType(status) {
  if (status === 'ok') return 'success'
  if (status === 'down') return 'danger'
  return 'warning'
}

const healthText = (s) => {
  if (s === 'ok') return '正常'
  if (s === 'down') return '异常'
  return '启动中'
}
const CHECK_NAMES = {
  chroma: '文档检索引擎', embedding: '向量模型', reranker: '重排序模型',
  llm: '大模型服务', budget: '资源预算', vector_store: '向量数据库',
}
// 组件详情（折叠区）：中文短义 + 英文专名，专业且可读
const MODULE_NAMES = {
  embedding: '向量模型 Embedding', vector_store: '向量数据库 Vector Store',
  retriever: '文档检索 Retriever', reranker: '重排序 Reranker',
  evaluator: '检索评估 Evaluator', rewriter: '问题改写 Rewriter',
  detector: '幻觉检测 Detector', llm: '大模型 LLM',
}
const PROVIDER_NAMES = {
  local_bge_v15: 'bge-small-zh-v1.5', chroma: 'Chroma',
  bge_reranker: 'bge-reranker', semantic_v2: 'Semantic v2（向量检索）',
  semantic: 'Semantic', llm: 'LLM', deepseek: 'DeepSeek',
  stub: 'Stub（离线模拟）', hybrid_rrf: 'Hybrid（RRF 融合）',
}
const checkName = (k) => CHECK_NAMES[k] || k
const moduleName = (k) => MODULE_NAMES[k] || k
const providerName = (p) => PROVIDER_NAMES[p] || p
</script>

<template>
  <div>
    <div class="card">
      <div class="row">
        <h2 style="flex: 1">设置总览</h2>
        <el-button @click="loadHealth(); loadPlugins()">刷新</el-button>
      </div>
      <div v-if="health" class="row">
        <el-tag :type="checkType(health.status)" size="large">服务状态：{{ healthText(health.status) }}</el-tag>
        <el-tag v-for="(v, k) in health.checks" :key="k" :type="checkType(v)" size="small">{{ checkName(k) }}：{{ healthText(v) }}</el-tag>
      </div>
      <div v-if="health && health.status !== 'ok'" class="muted" style="margin-top: 8px">
        部分组件正在启动/加载中，此处每 10 秒自动刷新，全部就绪后无需手动点击。
      </div>
    </div>

    <div class="card">
      <div class="row">
        <h2 style="flex: 1">认证与连接</h2>
        <el-button v-if="!isPersonalMode()" @click="logout">退出登录</el-button>
        <span v-else class="muted">个人模式，无需登录</span>
      </div>
      <div class="row">
        <el-tag v-if="isPersonalMode()" type="success" size="large">当前模式：个人模式 · 本机即管理员</el-tag>
        <el-tag v-else size="large">
          当前用户：{{ store.currentUser && (store.currentUser.name || store.currentUser.username) }}
          （{{ store.currentUser && store.currentUser.role === 'admin' ? '管理员' : '普通用户' }}）
        </el-tag>
      </div>
      <el-collapse>
        <el-collapse-item title="高级设置">
          <div class="row">
            <span style="width: 90px">外部后端：</span>
            <el-input v-model="externalUrl" style="width: 320px"
                      placeholder="如 http://127.0.0.1:9000（留空=本机/内置拉起）" />
            <el-button @click="saveExternal">保存</el-button>
          </div>
          <div class="muted">桌面模式：外部后端保存后重启应用生效；Web 模式需目标后端同源或开启 CORS。</div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <div v-if="isPersonalMode()" class="card">
      <div class="muted" style="margin: 0">
        个人模式无需登录，本机即管理员，全部功能均可使用。
        「用户管理」为团队模式专属功能：由管理员在服务端开启认证后使用，详见产品说明书。
      </div>
    </div>

    <div v-if="isAdmin() && !isPersonalMode()" class="card">
      <div class="row">
        <h2 style="flex: 1">用户管理</h2>
        <el-button @click="importDialog = true">批量导入</el-button>
        <el-button type="primary" @click="userDialog = true">新建用户</el-button>
      </div>
      <el-table :data="users" size="small" border>
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column prop="role" label="角色" width="90" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" @click="resetPassword(row.user_id)">重置密码</el-button>
            <el-button size="small" @click="changeRole(row.user_id, row.username, row.role === 'admin' ? 'user' : 'admin')">
              设为{{ row.role === 'admin' ? '普通' : '管理员' }}
            </el-button>
            <el-button size="small" type="danger"
                       :disabled="row.username === (store.currentUser && store.currentUser.username)"
                       @click="removeUser(row.user_id, row.username)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <el-collapse>
        <el-collapse-item title="组件详情（技术信息）">
          <el-table :data="Object.entries(plugins).map(([m, p]) => ({ module: m, provider: p }))" size="small" border>
            <el-table-column label="组件">
              <template #default="{ row }">{{ moduleName(row.module) }}</template>
            </el-table-column>
            <el-table-column label="当前模型">
              <template #default="{ row }">{{ providerName(row.provider) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </div>

    <el-dialog v-model="importDialog" title="批量导入用户" width="480px">
      <div class="muted" style="margin-bottom: 12px">
        列顺序：用户名, 姓名, 初始密码, 角色（user 或 admin，可省略默认普通用户）。文件须为
        UTF-8 编码的 CSV；先全量校验，全部合法才会写入。
      </div>
      <div class="row" style="margin-bottom: 14px">
        <el-button @click="downloadTemplate">下载模板</el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".csv" :on-change="onImportPick">
          <el-button type="primary" :loading="importing">选择 CSV 文件并导入</el-button>
        </el-upload>
      </div>
      <div v-if="importCreated" style="color: #1d7a45; margin-bottom: 8px">
        导入成功：{{ importCreated }} 个用户
      </div>
      <div v-if="importErrors.length" style="color: #c74f3f">
        <div style="margin-bottom: 4px">导入失败明细（未写入任何用户）：</div>
        <div v-for="(e, i) in importErrors" :key="i" style="font-size: 13px; line-height: 1.8">
          {{ e.row ? `第 ${e.row} 行：` : '' }}{{ e.message }}
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="userDialog" title="新建用户" width="420px">
      <el-form label-width="80px">
        <el-form-item label="用户名"><el-input v-model="userForm.username" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="userForm.name" /></el-form-item>
        <el-form-item label="初始密码"><el-input v-model="userForm.password" type="password" show-password /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingUser" @click="createUser">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
