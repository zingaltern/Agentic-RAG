<script setup>
/**
 * 应用外壳：启动态 → 登录态探测 → 侧栏 + 主区。
 * - booting：认证探测完成前只显示品牌启动页，避免「先闪登录页再跳回」的老问题
 * - bootError：后端不可达时给出明确的重试入口，而不是把用户丢在登录页
 * - 全局快捷键：⌘K 命令面板 / ? 快捷键 / Esc 关闭 / ⌘\ 折叠侧栏
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { DataLine, Menu, RefreshRight, Search, WarningFilled } from '@element-plus/icons-vue'
import AppSidebar from './components/AppSidebar.vue'
import CommandPalette from './components/CommandPalette.vue'
import ErrorBar from './components/ErrorBar.vue'
import ShortcutsSheet from './components/ShortcutsSheet.vue'
import LoginView from './views/LoginView.vue'
import { api } from './api'
import { useAppStore } from './stores/app'
import { openCommand, mobileNavOpen, shortcutsOpen, toggleCommand, openShortcuts, resetUiState } from './composables/useUi'
import { useIsMobile } from './composables/useMedia'
import { resetKbs } from './composables/useKbs'
import { abortQuery } from './composables/useChatQuery'

const store = useAppStore()
const route = useRoute()
const isMobile = useIsMobile()

const booting = ref(true)
const bootError = ref('')
const collapsed = ref(false)

const appTitle = computed(() => (route.meta && route.meta.title) || '智能知识库')

/** 首启动：探测登录态（40101=团队未登录，40001=个人模式，50001=服务不可达）。 */
async function probe() {
  booting.value = true
  bootError.value = ''
  const res = await api('/api/auth/me', { silent: true, timeout: 12000 })
  if (res.code === 0) {
    store.setAuth(res.data.user)
  } else if (res.code === 40101) {
    // 未登录：顺带清掉本机残留会话（token 已无效时不能继续沿用旧 session/kb）
    store.logout()
  } else if (res.code === 40001) {
    store.setAuth({ user_id: 'local', username: 'local', name: '个人模式', role: 'admin' })
  } else if (res.code === 50001) {
    bootError.value = res.message || '无法连接后端服务'
    store.clearAuth()
  } else {
    store.clearAuth()
  }
  booting.value = false
}

function isTypingTarget(el) {
  if (!el) return false
  const tag = (el.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable
}

function onKeydown(e) {
  const mod = e.metaKey || e.ctrlKey
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    toggleCommand()
    return
  }
  if (mod && e.key === '\\') {
    e.preventDefault()
    collapsed.value = !collapsed.value
    return
  }
  if (e.key === 'Escape') {
    if (shortcutsOpen.value) { shortcutsOpen.value = false; return }
    if (mobileNavOpen.value) { mobileNavOpen.value = false; return }
    return
  }
  if (e.key === '?' && !isTypingTarget(document.activeElement) && !mod) {
    e.preventDefault()
    openShortcuts()
  }
}

/**
 * 身份变化（登录 / 登出 / 401 自动登出）时清空「与用户绑定」的缓存：
 * 知识库列表（含私有库名）、在途问答请求。放在外壳层编排，避免 store 反向依赖 composable。
 */
watch(
  () => (store.currentUser ? store.currentUser.user_id : null),
  (next, prev) => {
    if (next === prev) return
    resetKbs()
    resetUiState()
    abortQuery()
    store.chat.loading = false
  },
)

onMounted(() => {
  probe()
  window.addEventListener('keydown', onKeydown)
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <el-config-provider :locale="zhCn">
    <!-- 启动页：认证探测期间不渲染登录页，避免闪烁 -->
    <div v-if="booting" class="boot" role="status" aria-live="polite">
    <span class="boot-mark"><el-icon><DataLine /></el-icon></span>
    <p class="boot-title">Agentic RAG</p>
    <p class="boot-sub">正在连接知识库…</p>
    <span class="boot-bar"><i /></span>
  </div>

  <!-- 后端不可达：明确失败原因 + 重试，而不是让用户面对空登录页 -->
  <div v-else-if="bootError" class="boot boot--error">
    <span class="boot-mark boot-mark--error"><el-icon><WarningFilled /></el-icon></span>
    <p class="boot-title">无法连接服务</p>
    <p class="boot-sub">{{ bootError }}</p>
    <p class="boot-hint">请确认后端已启动（默认 127.0.0.1:8000），或到设置页配置外部后端地址。</p>
    <div class="boot-actions">
      <el-button type="primary" @click="probe">
        <el-icon style="margin-right: 6px"><RefreshRight /></el-icon>重试连接
      </el-button>
      <el-button @click="bootError = ''">仍然进入登录页</el-button>
    </div>
  </div>

  <LoginView v-else-if="!store.authenticated" />

  <div v-else class="app-shell" :class="{ 'is-collapsed': collapsed, 'is-mobile': isMobile }">
    <AppSidebar :collapsed="collapsed && !isMobile" />

    <div v-if="isMobile && mobileNavOpen" class="nav-backdrop" @click="mobileNavOpen = false" />

    <div class="app-main">
      <!-- 移动端顶栏：汉堡 + 当前页 + 搜索 -->
      <header v-if="isMobile" class="mobile-bar">
        <button type="button" class="btn-icon" aria-label="打开导航" @click="mobileNavOpen = true">
          <el-icon><Menu /></el-icon>
        </button>
        <span class="mobile-title">{{ appTitle }}</span>
        <button type="button" class="btn-icon" aria-label="打开命令面板" @click="openCommand">
          <el-icon><Search /></el-icon>
        </button>
      </header>

      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </div>

    <CommandPalette />
    <ShortcutsSheet />
    <ErrorBar />
  </div>
  </el-config-provider>
</template>

<style scoped>
/* ---------- 启动 / 错误页 ---------- */
.boot {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
  text-align: center;
  background:
    radial-gradient(900px 420px at 50% -10%, var(--accent-soft), transparent 70%),
    var(--bg);
}
.boot-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin-bottom: 10px;
  border-radius: 18px;
  font-size: 26px;
  color: #fff;
  background: var(--accent-gradient);
  box-shadow: 0 12px 32px var(--accent-ring), inset 0 1px 0 rgba(255, 255, 255, 0.4);
  animation: boot-pulse 1.8s var(--ease) infinite;
}
.boot-mark--error {
  background: var(--danger-soft);
  color: var(--danger);
  box-shadow: inset 0 0 0 1px var(--danger);
  animation: none;
}
@keyframes boot-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.04); }
}
.boot-title { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
.boot-sub { margin: 0; font-size: 13px; color: var(--text-secondary); }
.boot-hint { margin: 4px 0 0; max-width: 420px; font-size: 12px; line-height: 1.6; color: var(--text-tertiary); }
.boot-actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; justify-content: center; }
.boot-bar {
  display: block;
  width: 132px;
  height: 4px;
  margin-top: 14px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--fill-2);
}
.boot-bar i {
  display: block;
  width: 40%;
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  animation: boot-slide 1.3s var(--ease) infinite;
}
@keyframes boot-slide {
  0% { transform: translateX(-110%); }
  100% { transform: translateX(330%); }
}

/* ---------- 外壳 ---------- */
.app-shell { position: relative; }
.nav-backdrop {
  position: fixed;
  inset: 0;
  z-index: 15;
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(2px);
  animation: fade-in 200ms var(--ease) both;
}
.mobile-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--topbar-h);
  padding: 0 10px;
  border-bottom: 1px solid var(--hairline);
  background: var(--surface-translucent);
  backdrop-filter: saturate(180%) blur(20px);
  z-index: 10;
}
.mobile-title {
  flex: 1;
  text-align: center;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
</style>
