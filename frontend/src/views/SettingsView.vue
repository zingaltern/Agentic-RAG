<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import { clearToken } from '../api/base'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()
const health = ref(null)
const plugins = ref({})
const externalUrl = ref('')
const users = ref([])
const userDialog = ref(false)
const userForm = ref({ username: '', name: '', password: '', role: 'user' })
const savingUser = ref(false)

const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'

onMounted(() => { loadHealth(); loadPlugins(); loadExternal(); if (isAdmin()) loadUsers() })

async function loadHealth() {
  const res = await api('/api/health')
  if (res.code === 0) { health.value = res.data; store.setTraceId(res.trace_id) }
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
  try { externalUrl.value = localStorage.getItem('external_base') || '' } catch { externalUrl.value = '' }
}

function logout() {
  clearToken()
  store.clearAuth()
  router.push('/login')
}

function saveExternal() {
  const url = externalUrl.value.trim()
  try { localStorage.setItem('external_base', url) } catch { /* ignore */ }
  if (window.desktop && window.desktop.setExternalBase) {
    window.desktop.setExternalBase(url)
    store.pushError('外部后端已保存：重启桌面应用后生效（届时不再自动拉起本地服务）')
  } else {
    store.pushError('外部后端已保存：Web 模式需后端开启 CORS 或同源部署')
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
    store.pushError(`已创建用户：${userForm.value.username}`)
    userDialog.value = false
    userForm.value = { username: '', name: '', password: '', role: 'user' }
    loadUsers()
  }
}

async function resetPassword(userId) {
  const { value } = await ElMessageBox.prompt('请输入新密码（至少 6 位）', '重置密码', { inputPattern: /^.{6,}$/, inputErrorMessage: '密码至少 6 位' })
  const res = await api(`/api/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: value }) })
  if (res.code === 0) store.pushError('密码已重置')
}
async function changeRole(userId, role) {
  const res = await api(`/api/users/${userId}/role`, { method: 'POST', body: JSON.stringify({ role }) })
  if (res.code === 0) { store.pushError('角色已更新'); loadUsers() }
}
async function removeUser(userId, username) {
  const ok = await ElMessageBox.confirm(`确定删除用户 ${username}？`, '删除用户', { type: 'warning' }).catch(() => false)
  if (!ok) return
  const res = await api(`/api/users/${userId}`, { method: 'DELETE' })
  if (res.code === 0) { store.pushError('用户已删除'); loadUsers() }
}

function checkType(status) {
  if (status === 'ok') return 'success'
  if (status === 'down') return 'danger'
  return 'warning'
}
</script>

<template>
  <div>
    <div class="card">
      <div class="row">
        <h2 style="flex: 1">⑤ 设置总览</h2>
        <el-button @click="loadHealth(); loadPlugins()">刷新</el-button>
      </div>
      <div v-if="health" class="row">
        <el-tag :type="checkType(health.status)" size="large">健康状态：{{ health.status }}</el-tag>
        <el-tag v-for="(v, k) in health.checks" :key="k" :type="checkType(v)" size="small">{{ k }}: {{ v }}</el-tag>
      </div>
      <div class="trace-line" v-if="store.lastTraceId">最近 trace_id：{{ store.lastTraceId }}（服务端 JSON 审计日志同号）</div>
    </div>

    <div class="card">
      <h2>认证与连接（V5.1）</h2>
      <div class="row">
        <el-tag size="large">
          当前用户：{{ store.currentUser && (store.currentUser.name || store.currentUser.username) }}
          （{{ store.currentUser && store.currentUser.role }}）
        </el-tag>
        <el-button @click="logout">退出登录</el-button>
      </div>
      <div class="row">
        <span style="width: 90px">外部后端：</span>
        <el-input v-model="externalUrl" style="width: 320px"
                  placeholder="如 http://127.0.0.1:9000（留空=本机/内置拉起）" />
        <el-button @click="saveExternal">保存</el-button>
      </div>
      <div class="muted">桌面模式：外部后端保存后重启应用生效；Web 模式需目标后端同源或开启 CORS。</div>
    </div>

    <div v-if="isAdmin()" class="card">
      <div class="row">
        <h2 style="flex: 1">用户管理（仅管理员）</h2>
        <el-button type="primary" @click="userDialog = true">新建用户</el-button>
      </div>
      <el-table :data="users" size="small" border>
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column prop="role" label="角色" width="90" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" @click="resetPassword(row.user_id)">重置密码</el-button>
            <el-button size="small" @click="changeRole(row.user_id, row.role === 'admin' ? 'user' : 'admin')">
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
      <h2>插件 provider（/api/plugins/status）</h2>
      <el-table :data="Object.entries(plugins).map(([m, p]) => ({ module: m, provider: p }))" size="small" border>
        <el-table-column prop="module" label="模块" />
        <el-table-column prop="provider" label="当前 provider" />
      </el-table>
    </div>

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
