<script setup>
/**
 * 侧栏（Apple 式毛玻璃导航）：品牌 / 命令入口 / 导航 / 当前知识库 / 用户与外观。
 * 移动端由 App.vue 以抽屉方式复用同一组件。
 */
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatDotRound, Collection, DataLine, Document, FolderOpened, Moon, QuestionFilled, Search, Setting, Sunny,
} from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import { useKbs } from '../composables/useKbs'
import { resolvedTheme, cycleTheme, themeMode } from '../composables/useTheme'
import { mobileNavOpen, openCommand, openShortcuts } from '../composables/useUi'
import { useIsMobile } from '../composables/useMedia'
import { initials } from '../utils/format'

defineProps({ collapsed: { type: Boolean, default: false } })

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const store = useAppStore()
const { kbs, load } = useKbs()

const NAV = [
  { path: '/', label: '问答控制台', icon: ChatDotRound, hint: '知识库问答与溯源' },
  { path: '/documents', label: '文档管理', icon: Document, hint: '上传与维护文档' },
  { path: '/kbs', label: '知识库管理', icon: FolderOpened, hint: '多库隔离与权限' },
  { path: '/settings', label: '设置总览', icon: Setting, hint: '外观、连接与用户' },
]

const isPersonal = computed(() => store.currentUser && store.currentUser.user_id === 'local')
const displayName = computed(() => {
  if (isPersonal.value) return '本机管理员'
  const u = store.currentUser
  if (!u) return '未登录'
  return u.name || u.username || '用户'
})
const avatarText = computed(() => (isPersonal.value ? '本' : initials(displayName.value)))
const roleText = computed(() => {
  if (isPersonal.value) return '个人模式'
  return store.currentUser && store.currentUser.role === 'admin' ? '管理员' : '普通用户'
})
const themeLabel = computed(() => ({ light: '浅色', dark: '深色', system: '跟随系统' }[themeMode.value] || '跟随系统'))

function isActive(path) {
  return path === '/' ? route.path === '/' : route.path.startsWith(path)
}

function go(path) {
  mobileNavOpen.value = false
  if (route.path !== path) router.push(path)
}

function pickKb(kbId) {
  store.setKbId(kbId)
  mobileNavOpen.value = false
  if (route.path !== '/') router.push('/')
}

function toggleTheme() {
  const next = cycleTheme()
  store.pushInfo(`外观：${{ light: '浅色', dark: '深色', system: '跟随系统' }[next]}`)
}

function openHelp() {
  mobileNavOpen.value = false
  openShortcuts()
}

onMounted(() => load())
</script>

<template>
  <aside
    class="sidebar"
    :class="{ 'is-open': mobileNavOpen, 'is-collapsed': collapsed }"
    :inert="isMobile && !mobileNavOpen"
    aria-label="主导航"
  >
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">
        <el-icon><DataLine /></el-icon>
      </span>
      <span class="brand-text">
        <b>Agentic RAG</b>
        <small>智能知识库</small>
      </span>
    </div>

    <button type="button" class="command-trigger" @click="openCommand">
      <el-icon><Search /></el-icon>
      <span>搜索或跳转</span>
      <span class="kbd">⌘K</span>
    </button>

    <nav class="nav">
      <p class="nav-label">工作区</p>
      <button
        v-for="item in NAV"
        :key="item.path"
        type="button"
        class="nav-item"
        :class="{ 'is-active': isActive(item.path) }"
        :aria-current="isActive(item.path) ? 'page' : undefined"
        :aria-label="item.label"
        @click="go(item.path)"
      >
        <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
        <span class="nav-text">
          <span class="nav-title">{{ item.label }}</span>
          <small>{{ item.hint }}</small>
        </span>
      </button>
    </nav>

    <div v-if="kbs.length" class="nav nav--kb">
      <p class="nav-label">
        知识库
        <button type="button" class="mini-link" @click="go('/kbs')">管理</button>
      </p>
      <button
        v-for="kb in kbs.slice(0, 4)"
        :key="kb.kb_id"
        type="button"
        class="kb-item"
        :class="{ 'is-current': kb.kb_id === store.kbId }"
        :title="kb.description || kb.name"
        @click="pickKb(kb.kb_id)"
      >
        <el-icon><Collection /></el-icon>
        <span class="truncate">{{ kb.name }}</span>
        <span v-if="kb.kb_id === store.kbId" class="kb-dot" />
      </button>
      <p v-if="kbs.length > 4" class="kb-more muted">另有 {{ kbs.length - 4 }} 个知识库</p>
    </div>

    <div class="sidebar-foot">
      <div class="user-chip">
        <span class="avatar">{{ avatarText }}</span>
        <span class="user-text">
          <b class="truncate">{{ displayName }}</b>
          <small>{{ roleText }}</small>
        </span>
      </div>
      <div class="foot-actions">
        <button type="button" class="btn-icon" :title="`外观：${themeLabel}`" :aria-label="`切换外观，当前${themeLabel}`" @click="toggleTheme">
          <el-icon><Moon v-if="resolvedTheme === 'light'" /><Sunny v-else /></el-icon>
        </button>
        <button type="button" class="btn-icon" title="键盘快捷键（?）" aria-label="键盘快捷键" @click="openHelp">
          <el-icon><QuestionFilled /></el-icon>
        </button>
      </div>
    </div>
    <p class="version">v1.0 · 本地优先 · 可溯源</p>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 12px 12px;
  background: var(--surface-translucent);
  backdrop-filter: saturate(180%) blur(24px);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  border-right: 1px solid var(--hairline);
  z-index: 20;
}

.brand { display: flex; align-items: center; gap: 10px; padding: 4px 6px 2px; }
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 11px;
  font-size: 17px;
  color: #fff;
  background: var(--accent-gradient);
  box-shadow: 0 6px 16px var(--accent-ring), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.brand-text b { font-size: 14.5px; font-weight: 700; letter-spacing: -0.01em; }
.brand-text small { font-size: 11px; color: var(--text-tertiary); }

.command-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  background: var(--fill-1);
  color: var(--text-tertiary);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}
.command-trigger:hover { background: var(--fill-2); color: var(--text-secondary); }
.command-trigger .kbd { margin-left: auto; }

.nav { display: flex; flex-direction: column; gap: 2px; }
.nav-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 6px 8px 4px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-quaternary);
}
.mini-link {
  border: none;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 10.5px;
  color: var(--accent);
  cursor: pointer;
}
.mini-link:hover { text-decoration: underline; }

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.nav-item:hover { background: var(--fill-1); color: var(--text); }
.nav-item.is-active {
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent-soft-2);
}
.nav-icon { font-size: 16px; flex: none; }
.nav-text { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.nav-title { font-size: 13px; font-weight: 500; }
.nav-text small { font-size: 10.5px; color: var(--text-quaternary); }
.nav-item.is-active .nav-text small { color: var(--accent); opacity: 0.75; }

.nav--kb { overflow: hidden; }
.kb-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-radius: var(--radius-xs);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}
.kb-item .el-icon { font-size: 13px; color: var(--text-quaternary); }
.kb-item:hover { background: var(--fill-1); color: var(--text); }
.kb-item.is-current { color: var(--accent); font-weight: 600; }
.kb-item.is-current .el-icon { color: var(--accent); }
.kb-dot { width: 6px; height: 6px; margin-left: auto; border-radius: 50%; background: var(--accent); }
.kb-more { margin: 4px 10px 0; }

.sidebar-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}
.user-chip { display: flex; align-items: center; gap: 9px; padding: 4px 6px; min-width: 0; }
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 50%;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-soft-2);
}
.user-text { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
.user-text b { font-size: 12.5px; font-weight: 600; }
.user-text small { font-size: 10.5px; color: var(--text-tertiary); }
.foot-actions { display: flex; gap: 2px; padding: 0 2px; }
.version { margin: 0; padding: 0 8px 2px; font-size: 10.5px; color: var(--text-quaternary); }

@media (max-width: 900px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: 280px;
    transform: translateX(-102%);
    transition: transform 300ms var(--ease);
    box-shadow: var(--shadow-3);
  }
  .sidebar.is-open { transform: translateX(0); }
}

/* ⌘\ 折叠：只留图标，腾出阅读宽度（宽屏） */
.sidebar.is-collapsed { width: 68px; padding-left: 8px; padding-right: 8px; align-items: center; }
.sidebar.is-collapsed .brand { justify-content: center; padding-left: 0; padding-right: 0; }
.sidebar.is-collapsed .brand-text,
.sidebar.is-collapsed .command-trigger span:not(.kbd),
.sidebar.is-collapsed .command-trigger .kbd,
.sidebar.is-collapsed .nav-label,
.sidebar.is-collapsed .nav-text,
.sidebar.is-collapsed .nav--kb,
.sidebar.is-collapsed .user-text,
.sidebar.is-collapsed .version,
.sidebar.is-collapsed .foot-actions { display: none; }
.sidebar.is-collapsed .command-trigger { justify-content: center; width: 40px; padding: 0; }
.sidebar.is-collapsed .nav-item { justify-content: center; padding: 9px 0; width: 44px; }
.sidebar.is-collapsed .user-chip { justify-content: center; padding: 4px 0; }
.sidebar.is-collapsed .sidebar-foot { align-items: center; width: 100%; }
</style>
