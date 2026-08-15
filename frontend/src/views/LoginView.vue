<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { setToken } from '../api/base'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()
const username = ref('')
const password = ref('')
const loading = ref(false)

async function submit() {
  if (!username.value.trim() || !password.value) {
    store.pushError('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    if (res.code === 0) {
      setToken(res.data.token)
      store.setAuth(res.data.user)
      store.pushError(`欢迎，${res.data.user.name || res.data.user.username}`)
      router.push('/')
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
