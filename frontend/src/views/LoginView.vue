<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { friendlyError } from '../api/index.js'
import { setToken } from '../api/base'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const loginError = ref('')

async function submit() {
  loginError.value = ''
  if (!username.value.trim() || !password.value) {
    loginError.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
      silent: true,   // 40001=个人模式（无登录功能）→ 下方分支直接恢复进入，不弹全局错
    })
    if (res.code === 0) {
      setToken(res.data.token)
      store.setAuth(res.data.user)
      store.pushInfo(`欢迎，${res.data.user.name || res.data.user.username}`)
      router.push('/')
    } else if (res.code === 40001) {
      // 个人模式（认证未启用）：无需账号——退出登录/误入时直接恢复本地管理员身份
      store.setAuth({ user_id: 'local', username: 'local', name: '个人模式', role: 'admin' })
      store.pushInfo('当前为个人模式，无需登录，已直接进入')
      router.push('/')
    } else {
      // silent 模式下 api() 不弹全局错（如口令错误 401xx、网络失败 50001），此处显式反馈
      loginError.value = friendlyError(res.code, res.message)
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>Agentic RAG 智能知识库</h2>
      <el-alert
        v-if="loginError"
        :title="loginError"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="username" placeholder="用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password
                    placeholder="密码" autocomplete="current-password"
                    @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap { display: flex; justify-content: center; align-items: center; height: 100%; }
.login-card { width: 360px; }
.login-card h2 { font-size: 18px; text-align: center; margin: 0 0 18px; }
</style>
