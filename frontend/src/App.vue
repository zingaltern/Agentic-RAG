<script setup>
import { onMounted } from 'vue'
import { ChatDotRound, DataAnalysis, Document, FolderOpened, Setting } from '@element-plus/icons-vue'
import ErrorBar from './components/ErrorBar.vue'
import LoginView from './views/LoginView.vue'
import { api } from './api'
import { useAppStore } from './stores/app'

const store = useAppStore()

onMounted(async () => {
  // V5.1：启动时探测登录态。40101=团队模式未登录（显示登录页）；
  // 40001=个人模式（auth_enabled=false，视为已登录管理员，行为与旧版一致）；
  // 其他错误码（如 50001 服务异常）不误判个人模式，保持未登录态。
  // silent：个人模式的 40001 属正常状态，禁止弹全局错误条。
  const res = await api('/api/auth/me', { silent: true })
  if (res.code === 0) {
    store.setAuth(res.data.user)
  } else if (res.code === 40101) {
    store.clearAuth()
  } else if (res.code === 40001) {
    store.setAuth({ user_id: 'local', username: 'local', name: '个人模式', role: 'admin' })
  } else if (res.code === 50001) {
    // 网络/服务不可达：不清登录态（避免刷新时误退出），但显式提示
    store.pushError('无法连接后端服务，请确认服务已启动')
  } else {
    store.clearAuth()
  }
})
</script>

<template>
  <LoginView v-if="!store.authenticated" />
  <el-container v-else class="layout">
    <el-aside width="210px" class="aside">
      <div class="logo">
        <span class="logo-icon"><el-icon><DataAnalysis /></el-icon></span>
        <span>
          <div class="logo-name">Agentic RAG</div>
          <div class="logo-sub">智能知识库</div>
        </span>
      </div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">
          <el-icon><ChatDotRound /></el-icon><span>问答控制台</span>
        </el-menu-item>
        <el-menu-item index="/documents">
          <el-icon><Document /></el-icon><span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/kbs">
          <el-icon><FolderOpened /></el-icon><span>知识库管理</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon><span>设置总览</span>
        </el-menu-item>
      </el-menu>
      <div class="footer-note">Agentic RAG v1.0 · 智能知识库</div>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
    <ErrorBar />
  </el-container>
</template>
