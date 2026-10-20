<script setup>
/**
 * 设置总览（分区卡片）
 * - 外观：亮/暗/跟随系统 + 四套强调色（写入 <html data-theme/data-accent>）
 * - 服务健康：/api/health 轮询（页面隐藏时暂停，回来后立即补一次，避免后台空转）
 * - 连接与账号：外部后端、当前身份、改密、退出登录
 * - 运行时组件：/api/plugins/status + /api/plugins/switch 模型热切换（管理员，失败自动回滚）
 * - 用户管理（团队模式管理员）：建号/改角色/重置密码/批量导入 CSV
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  Connection, Cpu, Download, Key, Moon, QuestionFilled, Refresh, Setting,
  Sunny, SwitchButton, User, UserFilled,
} from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import SegmentedControl from '../components/SegmentedControl.vue'
import { api, friendlyError, REQUEST_TIMEOUT_MS } from '../api'
import { getBaseUrl } from '../api/base'
import { useAppStore } from '../stores/app'
import {
  ACCENT_OPTIONS, THEME_OPTIONS, accent, setAccent, setThemeMode, themeMode,
} from '../composables/useTheme'
import { openShortcuts } from '../composables/useUi'
import { confirmDialog, promptPassword } from '../utils/confirm'
import { PASSWORD_HINT, PASSWORD_RULE, validatePassword } from '../utils/password'

const store = useAppStore()

const CHECK_NAMES = {
  chroma: '文档检索引擎', embedding: '向量模型', reranker: '重排序模型',
  llm: '大模型服务', budget: '资源预算', vector_store: '向量数据库',
}
const MODULE_NAMES = {
  embedding: '向量模型 Embedding', vector_store: '向量数据库 Vector Store',
  retriever: '文档检索 Retriever', reranker: '重排序 Reranker',
  evaluator: '检索评估 Evaluator', rewriter: '问题改写 Rewriter',
  detector: '幻觉检测 Detector', llm: '大模型 LLM',
}
const PROVIDER_NAMES = {
  local_bge_v15: 'bge-small-zh-v1.5', bge_m3: 'bge-m3', remote_api: '远程 Embedding API',
  chroma: 'Chroma', milvus: 'Milvus',
  hybrid_rrf: 'Hybrid（RRF 融合）', hybrid_rerank: 'Hybrid + 精排',
  identity: '不精排（identity）', bge_reranker: 'bge-reranker-v2-m3',
  rule: '规则（rule）', semantic_v2: '语义 v2', semantic: '语义（semantic）',
  llm: 'LLM 改写', deepseek: 'DeepSeek', ollama: 'Ollama', openai_compat: 'OpenAI 兼容',
}
/**
 * 下拉候选 = 实际已注册的实现（与 app/adapters/__init__.py 的 PluginRegistry.register 同步）。
 * 注意：ALLOWED_PROVIDERS 允许但未注册的 provider（bge_m3/remote_api/milvus/
 * hybrid_rerank/ollama/openai_compat）选中后会 40001「尚未装配实现」，因此不列入。
 * 新增适配器时，请同时更新此处。
 */
const PROVIDER_OPTIONS = {
  embedding: ['local_bge_v15'],
  vector_store: ['chroma'],
  retriever: ['hybrid_rrf'],
  reranker: ['identity', 'bge_reranker'],
  evaluator: ['rule', 'semantic_v2'],
  rewriter: ['rule', 'llm'],
  detector: ['citation', 'semantic'],
  llm: ['deepseek'],
}

const health = ref(null)
const healthLoading = ref(false)
const healthUpdatedAt = ref(null)
const autoRefresh = ref(true)
const plugins = ref({})
const pluginsError = ref('')
const switching = ref('')
const externalUrl = ref('')
const users = ref([])
const usersLoading = ref(false)
const usersError = ref('')

const userDialog = ref(false)
const userForm = ref({ username: '', name: '', password: '', role: 'user' })
const savingUser = ref(false)
const passwordDialog = ref(false)
const passwordForm = ref({ old: '', next: '', confirm: '' })
const savingPassword = ref(false)

const importDialog = ref(false)
const importing = ref(false)
const importCreated = ref(0)
const importErrors = ref([])

const isAdmin = computed(() => Boolean(store.currentUser && store.currentUser.role === 'admin'))
const isPersonalMode = computed(() => Boolean(store.currentUser && store.currentUser.user_id === 'local'))
const userName = computed(() => (store.currentUser && (store.currentUser.name || store.currentUser.username)) || '未登录')
const roleText = computed(() => (isPersonalMode.value ? '个人模式 · 本机即管理员' : (isAdmin.value ? '管理员' : '普通用户')))
const checks = computed(() => {
  const c = (health.value && health.value.checks) || {}
  return Object.entries(c).map(([key, status]) => ({
    key, status, label: CHECK_NAMES[key] || key,
  }))
})
const ready = computed(() => health.value && health.value.status === 'ok')

/* ---------------- 生命周期 ---------------- */
let pollTimer = null

onMounted(() => {
  loadHealth()
  loadPlugins()
  loadExternal()
  if (isAdmin.value && !isPersonalMode.value) loadUsers()
  pollTimer = setInterval(() => {
    if (document.hidden || !autoRefresh.value) return
    loadHealth({ silent: true })
  }, 10000)
  document.addEventListener('visibilitychange', onVisible)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisible)
})

function onVisible() {
  if (!document.hidden && autoRefresh.value) loadHealth({ silent: true })
}

/* ---------------- 健康 ---------------- */
async function loadHealth({ silent = false } = {}) {
  if (!silent) healthLoading.value = true
  try {
    const res = await api('/api/health', { silent: true })
    if (res.code === 0) {
      health.value = res.data
      healthUpdatedAt.value = new Date()
    } else if (!silent) {
      health.value = null
      store.pushError(`健康检查失败：${friendlyError(res.code, res.message)}`)
    }
  } finally {
    healthLoading.value = false
  }
}

/* ---------------- 插件 ---------------- */
async function loadPlugins() {
  const res = await api('/api/plugins/status', { silent: true })
  if (res.code === 0) {
    plugins.value = res.data.modules || {}
    pluginsError.value = ''
  } else if (res.code !== 40101) {
    pluginsError.value = friendlyError(res.code, res.message)
  }
}

async function switchProvider(module) {
  const strategy = plugins.value[module]
  switching.value = module
  try {
    const res = await api('/api/plugins/switch', {
      method: 'POST',
      silent: true,
      body: JSON.stringify({ module, strategy }),
    })
    if (res.code === 0) {
      store.pushInfo(`${MODULE_NAMES[module] || module} 已切换为 ${PROVIDER_NAMES[strategy] || strategy}`)
      await loadPlugins()
      await loadHealth({ silent: true })
    } else if (res.code !== 40101) {
      // 切换失败：服务端保持原 provider（回滚语义），重新拉取以还原界面
      store.pushError(`切换失败：${friendlyError(res.code, res.message)}`)
      await loadPlugins()
    }
  } finally {
    switching.value = ''
  }
}

/* ---------------- 连接 ---------------- */
function loadExternal() {
  if (window.desktop && window.desktop.getExternalBase) {
    window.desktop.getExternalBase()
      .then((url) => { externalUrl.value = url || '' })
      .catch(() => {})
  } else {
    try { externalUrl.value = localStorage.getItem('external_base') || '' } catch { externalUrl.value = '' }
  }
}

function saveExternal() {
  // 先取用户输入：旧实现的 loadExternal() 会把刚输入的内容覆盖回存储里的旧值
  const url = externalUrl.value.trim()
  externalUrl.value = url
  try { localStorage.setItem('external_base', url) } catch { /* ignore */ }
  if (window.desktop && window.desktop.setExternalBase) {
    window.desktop.setExternalBase(url)
    store.pushInfo('外部后端已保存：重启桌面应用后生效（届时不再自动拉起本地服务）')
  } else {
    store.pushInfo('外部后端已保存：Web 模式需目标后端同源或开启 CORS')
  }
}

async function testConnection() {
  const started = Date.now()
  const res = await api('/api/health', { silent: true, timeout: 8000 })
  const cost = Date.now() - started
  if (res.code === 0) {
    store.pushInfo(`连接正常（${cost} ms）· 版本 ${res.data.version || '—'} · 状态 ${res.data.status}`)
  } else {
    store.pushError(`连接失败：${friendlyError(res.code, res.message)}`)
  }
}

/* ---------------- 账号 ---------------- */
function logout() {
  // store.logout() 内部已清 Token/会话/知识库选择；这里不再重复 clearToken
  store.logout()
}

async function changePassword() {
  const f = passwordForm.value
  if (!f.old || !f.next) { store.pushError('请填写旧密码与新密码'); return }
  const weak = validatePassword(f.next)
  if (weak) { store.pushError(weak); return }
  if (f.next !== f.confirm) { store.pushError('两次输入的新密码不一致'); return }
  savingPassword.value = true
  try {
    const res = await api('/api/auth/change-password', {
      method: 'POST',
      silent: true,
      body: JSON.stringify({ old_password: f.old, new_password: f.next }),
    })
    if (res.code === 0) {
      // 服务端已递增 token_version：旧 Token 立即失效，继续停在设置页只会满屏 401。
      // store.logout() 清 Token/会话/kb，App 依据 authenticated 自动切登录视图。
      passwordDialog.value = false
      passwordForm.value = { old: '', next: '', confirm: '' }
      store.logout()
    } else if (res.code !== 40101) {
      // 直接展示后端中文原因（如“旧密码错误”“密码强度不足”），不重复包装
      store.pushError(res.message ? `修改失败：${res.message}` : '修改失败，请稍后重试')
    }
  } finally {
    savingPassword.value = false
  }
}

/* ---------------- 用户管理 ---------------- */
async function loadUsers() {
  usersLoading.value = true
  try {
    const res = await api('/api/users', { silent: true })
    if (res.code === 0) {
      users.value = res.data.users || []
      usersError.value = ''
    } else if (res.code !== 40101) {
      usersError.value = friendlyError(res.code, res.message)
    }
  } finally {
    usersLoading.value = false
  }
}

async function createUser() {
  if (!userForm.value.username.trim() || !userForm.value.password) {
    store.pushError('请输入用户名与初始密码')
    return
  }
  const weak = validatePassword(userForm.value.password)
  if (weak) { store.pushError(weak); return }
  savingUser.value = true
  try {
    const res = await api('/api/users', { method: 'POST', body: JSON.stringify(userForm.value) })
    if (res.code === 0) {
      store.pushInfo(`已创建用户：${userForm.value.username}`)
      userDialog.value = false
      userForm.value = { username: '', name: '', password: '', role: 'user' }
      loadUsers()
    }
  } finally {
    savingUser.value = false
  }
}

async function resetPassword(userId) {
  const value = await promptPassword('请输入新密码（至少 8 位，含字母和数字）', '重置密码', {
    inputPattern: PASSWORD_RULE,
    inputErrorMessage: '至少 8 位，且同时包含字母和数字',
  })
  if (!value) return
  const res = await api(`/api/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ new_password: value }),
  })
  if (res.code === 0) store.pushInfo('密码已重置')
}

async function changeRole(userId, username, newRole) {
  const ok = await confirmDialog(
    `确定将 ${username} 设为${newRole === 'admin' ? '管理员' : '普通用户'}？`,
    '变更角色',
  )
  if (!ok) return
  const res = await api(`/api/users/${userId}/role`, { method: 'POST', body: JSON.stringify({ role: newRole }) })
  if (res.code === 0) {
    store.pushInfo('角色已更新')
    loadUsers()
  }
}

async function removeUser(userId, username) {
  const ok = await confirmDialog(`确定删除用户 ${username}？`, '删除用户')
  if (!ok) return
  const res = await api(`/api/users/${userId}`, { method: 'DELETE' })
  if (res.code === 0) {
    store.pushInfo('用户已删除')
    loadUsers()
  }
}

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
      const res = await api('/api/users/import', { method: 'POST', body: form, silent: true })
      if (res.code === 0) {
        importCreated.value = res.data.created || 0
        store.pushInfo(`批量导入成功：${importCreated.value} 个用户`)
        loadUsers()
      } else if (res.code !== 40101) {
        importErrors.value = (res.data && res.data.errors) || [{ row: 0, message: res.message || '导入失败' }]
        store.pushError('批量导入失败：部分账号存在问题，未写入任何用户')
      }
    } finally {
      importing.value = false
    }
  }
  reader.readAsText(raw)
}

function statusType(status) {
  if (status === 'ok') return 'success'
  if (status === 'down') return 'danger'
  return 'warning'
}
const healthText = (s) => (s === 'ok' ? '正常' : s === 'down' ? '异常' : '启动中')
const moduleName = (k) => MODULE_NAMES[k] || k
const providerName = (p) => PROVIDER_NAMES[p] || p
</script>

<template>
  <div class="page-scroll">
    <div class="page">
      <PageHeader title="设置总览" subtitle="外观、服务状态、连接方式与账号权限，都在这一页">
        <template #actions>
          <el-button @click="loadHealth(); loadPlugins()">
            <el-icon style="margin-right: 6px"><Refresh /></el-icon>刷新
          </el-button>
        </template>
      </PageHeader>

      <!-- 服务状态 -->
      <section class="card">
        <div class="card-head">
          <h2 class="card-title" style="margin: 0">
            <span class="title-icon"><el-icon><Connection /></el-icon></span>服务状态
          </h2>
          <span class="spacer" />
          <span v-if="healthUpdatedAt" class="muted">更新于 {{ healthUpdatedAt.toLocaleTimeString('zh-CN') }}</span>
          <el-switch v-model="autoRefresh" size="small" active-text="自动刷新" />
        </div>

        <div v-if="health" class="health-body">
          <el-tag :type="statusType(health.status)" size="large" effect="light">
            {{ ready ? '全部就绪' : '部分组件启动中' }}
          </el-tag>
          <div class="checks">
            <span v-for="c in checks" :key="c.key" class="check" :class="`check--${statusType(c.status)}`">
              <i />
              {{ c.label }}
              <b>{{ healthText(c.status) }}</b>
            </span>
          </div>
        </div>
        <p v-else-if="healthLoading" class="muted">正在检查…</p>
        <p v-else class="muted">未获取到健康状态，请确认后端已启动。</p>
        <p v-if="health && !ready" class="muted hint">
          模型首次加载需要时间，此处每 10 秒自动刷新（页面隐藏时暂停）。
        </p>
      </section>

      <!-- 外观 -->
      <section class="card">
        <h2 class="card-title"><span class="title-icon"><el-icon><Setting /></el-icon></span>外观</h2>
        <div class="setting-row">
          <div class="setting-label">
            <b>主题</b>
            <small>跟随系统时会随 macOS / Windows 自动切换</small>
          </div>
          <SegmentedControl :model-value="themeMode" :options="THEME_OPTIONS" size="sm" @update:model-value="setThemeMode" />
        </div>
        <div class="setting-row">
          <div class="setting-label">
            <b>强调色</b>
            <small>影响按钮、选中态与引用标号</small>
          </div>
          <div class="swatches">
            <button
              v-for="a in ACCENT_OPTIONS"
              :key="a.id"
              type="button"
              class="swatch"
              :class="{ 'is-active': accent === a.id }"
              :style="{ background: a.color }"
              :aria-label="a.label"
              :title="a.label"
              @click="setAccent(a.id)"
            />
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-label">
            <b>提示</b>
            <small>浅色 ↔ 深色可用侧栏底部的月亮按钮一键切换</small>
          </div>
          <div class="row--tight">
            <el-button size="small" aria-label="切换到浅色外观" @click="setThemeMode('light')"><el-icon><Sunny /></el-icon></el-button>
            <el-button size="small" aria-label="切换到深色外观" @click="setThemeMode('dark')"><el-icon><Moon /></el-icon></el-button>
          </div>
        </div>
      </section>

      <!-- 连接与账号 -->
      <section class="card">
        <div class="card-head">
          <h2 class="card-title" style="margin: 0">
            <span class="title-icon"><el-icon><User /></el-icon></span>连接与账号
          </h2>
          <span class="spacer" />
          <el-button v-if="!isPersonalMode" size="small" @click="logout">
            <el-icon style="margin-right: 5px"><SwitchButton /></el-icon>退出登录
          </el-button>
        </div>

        <div class="account-row">
          <span class="account-avatar">{{ isPersonalMode ? '本' : userName.slice(-1) }}</span>
          <div class="account-text">
            <b>{{ userName }}</b>
            <small>{{ roleText }} · 地址 {{ getBaseUrl() || '同源（内置服务）' }}</small>
          </div>
          <el-button v-if="!isPersonalMode" size="small" @click="passwordDialog = true">
            <el-icon style="margin-right: 5px"><Key /></el-icon>修改密码
          </el-button>
        </div>
        <p v-if="isPersonalMode" class="muted">
          个人模式无需登录，本机即管理员；「用户管理」需在服务端开启认证后使用。
        </p>

        <div class="connect-row">
          <span class="connect-label">外部后端</span>
          <el-input v-model="externalUrl" placeholder="如 http://127.0.0.1:9000（留空 = 本机 / 桌面端内置服务）" clearable />
          <el-button @click="saveExternal">保存</el-button>
          <el-button @click="testConnection">测试连接</el-button>
        </div>
        <p class="muted">
          桌面端：保存后重启应用生效（届时不再自动拉起本地服务）；Web 端：目标后端需同源或开启 CORS。
          请求超时 {{ Math.round(REQUEST_TIMEOUT_MS / 1000) }} 秒。
        </p>
      </section>

      <!-- 运行时组件 -->
      <section class="card">
        <div class="card-head">
          <h2 class="card-title" style="margin: 0">
            <span class="title-icon"><el-icon><Cpu /></el-icon></span>运行时组件
          </h2>
          <span class="spacer" />
          <el-tag size="small" effect="plain">{{ isAdmin ? '可热切换（管理员）' : '只读' }}</el-tag>
        </div>
        <div v-if="pluginsError" class="inline-error">
          <span>组件状态加载失败：{{ pluginsError }}</span>
          <el-button size="small" text type="primary" @click="loadPlugins">重试</el-button>
        </div>
        <div class="plugin-grid">
          <div v-for="(provider, module) in plugins" :key="module" class="plugin-row">
            <span class="plugin-name">{{ moduleName(module) }}</span>
            <el-select
              v-model="plugins[module]"
              size="small"
              class="plugin-select"
              :disabled="!isAdmin || switching === module"
            >
              <el-option
                v-for="opt in (PROVIDER_OPTIONS[module] || [provider])"
                :key="opt"
                :label="providerName(opt)"
                :value="opt"
              />
            </el-select>
            <el-button
              size="small"
              type="primary"
              plain
              :loading="switching === module"
              :disabled="!isAdmin || switching === module"
              @click="switchProvider(module)"
            >应用</el-button>
          </div>
        </div>
        <p class="muted">
          热切换会重建依赖并原子替换；任一环节失败自动回滚到原 provider，不会让服务处于半配置状态。
          密钥类配置（api_key/secret）不允许热改。
        </p>
      </section>

      <!-- 用户管理 -->
      <section v-if="isAdmin && !isPersonalMode" class="card">
        <div class="card-head">
          <h2 class="card-title" style="margin: 0">
            <span class="title-icon"><el-icon><UserFilled /></el-icon></span>用户管理
          </h2>
          <span class="spacer" />
          <el-button size="small" @click="importDialog = true">
            <el-icon style="margin-right: 5px"><Download /></el-icon>批量导入
          </el-button>
          <el-button size="small" type="primary" @click="userDialog = true">新建用户</el-button>
        </div>
        <div v-if="usersError" class="inline-error">
          <span>用户列表加载失败：{{ usersError }}</span>
          <el-button size="small" text type="primary" @click="loadUsers">重试</el-button>
        </div>
        <el-table v-loading="usersLoading" :data="users" size="small">
          <el-table-column prop="username" label="用户名" min-width="120" />
          <el-table-column prop="name" label="姓名" min-width="110" />
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.role === 'admin' ? 'warning' : 'info'" effect="plain">
                {{ row.role === 'admin' ? '管理员' : '普通用户' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="250" align="right">
            <template #default="{ row }">
              <el-button size="small" text @click="resetPassword(row.user_id)">重置密码</el-button>
              <el-button size="small" text @click="changeRole(row.user_id, row.username, row.role === 'admin' ? 'user' : 'admin')">
                设为{{ row.role === 'admin' ? '普通' : '管理员' }}
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :disabled="row.username === (store.currentUser && store.currentUser.username)"
                @click="removeUser(row.user_id, row.username)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- 关于 -->
      <section class="card about">
        <div class="about-left">
          <span class="about-mark"><el-icon><Setting /></el-icon></span>
          <div>
            <b>Agentic RAG · v1.0</b>
            <p class="muted">本地优先部署 · 支持 Web 与 Electron 桌面端 · 回答可溯源</p>
          </div>
        </div>
        <el-button @click="openShortcuts">
          <el-icon style="margin-right: 6px"><QuestionFilled /></el-icon>键盘快捷键
        </el-button>
      </section>
    </div>

    <!-- 修改密码 -->
    <el-dialog v-model="passwordDialog" title="修改密码" width="400px" align-center>
      <el-form label-position="top">
        <el-form-item label="旧密码"><el-input v-model="passwordForm.old" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="passwordForm.next" type="password" show-password :placeholder="PASSWORD_HINT" /></el-form-item>
        <el-form-item label="确认新密码"><el-input v-model="passwordForm.confirm" type="password" show-password /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingPassword" @click="changePassword">更新密码</el-button>
      </template>
    </el-dialog>

    <!-- 新建用户 -->
    <el-dialog v-model="userDialog" title="新建用户" width="420px" align-center>
      <el-form label-position="top">
        <el-form-item label="用户名"><el-input v-model="userForm.username" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="userForm.name" /></el-form-item>
        <el-form-item label="初始密码"><el-input v-model="userForm.password" type="password" show-password :placeholder="PASSWORD_HINT" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role" style="width: 100%">
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

    <!-- 批量导入 -->
    <el-dialog v-model="importDialog" title="批量导入用户" width="480px" align-center>
      <p class="muted" style="margin-top: 0">
        列顺序：用户名, 姓名, 初始密码, 角色（user 或 admin，可省略默认普通用户）。
        文件须为 UTF-8 CSV；先全量校验，全部合法才会写入。
      </p>
      <div class="row" style="margin-bottom: 12px">
        <el-button @click="downloadTemplate">下载模板</el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".csv" :on-change="onImportPick">
          <el-button type="primary" :loading="importing">选择 CSV 并导入</el-button>
        </el-upload>
      </div>
      <div v-if="importCreated" class="import-ok">导入成功：{{ importCreated }} 个用户</div>
      <div v-if="importErrors.length" class="import-err">
        <div style="margin-bottom: 4px">导入失败明细（未写入任何用户）：</div>
        <div v-for="(e, i) in importErrors" :key="i">{{ e.row ? `第 ${e.row} 行：` : '' }}{{ e.message }}</div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }

/* 健康 */
.health-body { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.checks { display: flex; flex-wrap: wrap; gap: 8px; }
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: var(--radius-pill);
  background: var(--fill-1);
  font-size: 12px;
  color: var(--text-secondary);
}
.check i { width: 6px; height: 6px; border-radius: 50%; background: var(--text-tertiary); }
.check b { font-weight: 600; color: var(--text); }
.check--success i { background: var(--success); box-shadow: 0 0 0 3px var(--success-soft); }
.check--danger i { background: var(--danger); box-shadow: 0 0 0 3px var(--danger-soft); }
.check--warning i { background: var(--warning); box-shadow: 0 0 0 3px var(--warning-soft); }
.hint { margin: 10px 0 0; }

/* 设置行 */
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--hairline);
}
.setting-row:last-child { border-bottom: none; padding-bottom: 0; }
.setting-label { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.setting-label b { font-size: 13px; font-weight: 600; }
.setting-label small { font-size: 11.5px; color: var(--text-tertiary); }
.swatches { display: flex; gap: 8px; }
.swatch {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--surface);
  box-shadow: 0 0 0 1px var(--hairline-strong);
  cursor: pointer;
  transition: transform var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.swatch:hover { transform: scale(1.08); }
.swatch.is-active { box-shadow: 0 0 0 2px var(--accent), 0 0 0 4px var(--accent-soft); }

/* 账号 */
.account-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0 14px;
  border-bottom: 1px solid var(--hairline);
}
.account-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: none;
  border-radius: 50%;
  font-size: 15px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-soft-2);
}
.account-text { display: flex; flex-direction: column; flex: 1; min-width: 0; line-height: 1.4; }
.account-text b { font-size: 13.5px; }
.account-text small { font-size: 11.5px; color: var(--text-tertiary); word-break: break-all; }

.connect-row { display: flex; align-items: center; gap: 8px; margin: 14px 0 6px; flex-wrap: wrap; }
.connect-label { font-size: 12.5px; color: var(--text-secondary); flex: none; width: 74px; }
.connect-row :deep(.el-input) { flex: 1; min-width: 220px; }

/* 插件 */
.plugin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px 22px; }
.plugin-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
.plugin-name { flex: 1; font-size: 12.5px; color: var(--text-secondary); min-width: 0; }
.plugin-select { width: 168px; }

/* 关于 */
.about { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.about-left { display: flex; align-items: center; gap: 12px; }
.about-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  color: #fff;
  background: var(--accent-gradient);
  box-shadow: 0 6px 16px var(--accent-ring);
}
.about-left b { font-size: 13.5px; }
.about-left p { margin: 2px 0 0; }

.inline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
}
.import-ok { color: var(--success); font-size: 13px; margin-bottom: 8px; }
.import-err { color: var(--danger); font-size: 12.5px; line-height: 1.8; }

@media (max-width: 640px) {
  .setting-row { flex-direction: column; align-items: flex-start; gap: 8px; }
  .connect-label { width: auto; }
  .plugin-row { flex-wrap: wrap; }
  .plugin-select { width: 100%; }
}
</style>
