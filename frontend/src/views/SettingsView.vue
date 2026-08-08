<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const health = ref(null)
const plugins = ref({})

onMounted(() => { loadHealth(); loadPlugins() })

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
      <h2>插件 provider（/api/plugins/status）</h2>
      <el-table :data="Object.entries(plugins).map(([m, p]) => ({ module: m, provider: p }))" size="small" border>
        <el-table-column prop="module" label="模块" />
        <el-table-column prop="provider" label="当前 provider" />
      </el-table>
    </div>
  </div>
</template>
