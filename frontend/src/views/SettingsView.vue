<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { getApiKey } from '../api/base'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const health = ref(null)
const plugins = ref({})
const apiKey = ref('')
const externalUrl = ref('')

onMounted(() => { loadHealth(); loadPlugins(); apiKey.value = getApiKey(); loadExternal() })

async function loadHealth() {
  const res = await api('/api/health')
  if (res.code === 0) {
    health.value = res.data
    store.setTraceId(res.trace_id)
  }
}
async function loadPlugins() {
  const res = await api('/api/plugins/status')
  if (res.code === 0) plugins.value = res.data.modules || {}
}

function loadExternal() {
  try {
    externalUrl.value = localStorage.getItem('external_base') || ''
  } catch { externalUrl.value = '' }
}

function saveApiKey() {
  try {
    localStorage.setItem('api_key', apiKey.value.trim())
  } catch { /* ignore */ }
  store.pushError('API Key 已保存（本地持久化；服务端需 auth_enabled=true 生效）')
}

function saveExternal() {
  const url = externalUrl.value.trim()
  try {
    localStorage.setItem('external_base', url)
  } catch { /* ignore */ }
  // 桌面：经 preload IPC 写入 userData（重启后 main 决定不拉起本地后端）
  if (window.desktop && window.desktop.setExternalBase) {
    window.desktop.setExternalBase(url)
    store.pushError('外部后端已保存：重启桌面应用后生效（届时不再自动拉起本地服务）')
  } else {
    store.pushError('外部后端已保存：Web 模式需后端开启 CORS 或同源部署')
  }
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
        <el-tag :type="checkType(health.status)" size="large">
          健康状态：{{ health.status }}
        </el-tag>
        <el-tag v-for="(v, k) in health.checks" :key="k" :type="checkType(v)" size="small">
          {{ k }}: {{ v }}
        </el-tag>
      </div>
      <div class="trace-line" v-if="store.lastTraceId">最近 trace_id：{{ store.lastTraceId }}（服务端 JSON 审计日志同号）</div>
    </div>

    <div class="card">
      <h2>认证与连接（S1 / 外部后端）</h2>
      <div class="row">
        <span style="width: 90px">API Key：</span>
        <el-input v-model="apiKey" type="password" show-password style="width: 320px"
                  placeholder="服务端 auth_enabled=true 时填写（X-API-Key）" />
        <el-button @click="saveApiKey">保存 Key</el-button>
      </div>
      <div class="row">
        <span style="width: 90px">外部后端：</span>
        <el-input v-model="externalUrl" style="width: 320px"
                  placeholder="如 http://127.0.0.1:9000（留空=本机/内置拉起）" />
        <el-button @click="saveExternal">保存</el-button>
      </div>
      <div class="muted">桌面模式：外部后端保存后重启应用生效（不再自动拉起本地服务）；Web 模式需目标后端同源或开启 CORS。</div>
    </div>

    <div class="card">
      <h2>插件 provider（/api/plugins/status）</h2>
      <el-table :data="Object.entries(plugins).map(([m, p]) => ({ module: m, provider: p }))" size="small" border>
        <el-table-column prop="module" label="模块" />
        <el-table-column prop="provider" label="当前 provider" />
      </el-table>
    </div>
  </div>
</template>
