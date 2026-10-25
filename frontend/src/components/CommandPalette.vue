<script setup>
/**
 * ⌘K 命令面板（Apple Spotlight 式）：一处完成导航、会话操作、知识库切换与外观设置。
 * - 键盘：↑↓ 选择 / Enter 执行 / Esc 关闭
 * - 纯前端动作，全部走既有 API/路由，不引入新后端契约
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Brush, ChatDotRound, Delete, Document, FolderOpened, MagicStick, Monitor, Moon,
  Plus, QuestionFilled, Refresh, Search, Setting, Sunny, SwitchButton,
} from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import { useKbs } from '../composables/useKbs'
import { ACCENT_OPTIONS, setAccent, setThemeMode } from '../composables/useTheme'
import { commandOpen, closeCommand, openShortcuts, requestComposerFocus } from '../composables/useUi'
import { abortQuery } from '../composables/useChatQuery'

const router = useRouter()
const store = useAppStore()
const { kbs, load } = useKbs()

const query = ref('')
const active = ref(0)
const inputRef = ref(null)
const listRef = ref(null)

function newChat() {
  abortQuery()   // 在途请求必须停：否则旧回答会落到新会话
  store.chat = { messages: [], loading: false, query: '' }
  store.resetSession()
  router.push('/')
  requestComposerFocus()
}

function clearChat() {
  abortQuery()
  store.chat = { messages: [], loading: false, query: '' }
  store.resetSession()
  store.pushInfo('已开始新会话')
}

function navigate(path) {
  router.push(path)
}

const groups = computed(() => {
  const nav = [
    { id: 'nav-query', label: '问答控制台', hint: '基于知识库提问', icon: ChatDotRound, keywords: 'chat query ask 问答', run: () => navigate('/') },
    { id: 'nav-docs', label: '文档管理', hint: '上传、检索与更新文档', icon: Document, keywords: 'document upload 文档 上传', run: () => navigate('/documents') },
    { id: 'nav-kbs', label: '知识库管理', hint: '新建知识库与权限', icon: FolderOpened, keywords: 'kb knowledge base 知识库', run: () => navigate('/kbs') },
    { id: 'nav-settings', label: '设置总览', hint: '外观、连接与用户', icon: Setting, keywords: 'setting config 设置', run: () => navigate('/settings') },
  ]

  const actions = [
    { id: 'act-new', label: '新对话', hint: '重置服务端会话并清空消息', icon: Plus, keywords: 'new chat 新建', run: newChat },
    { id: 'act-clear', label: '清空当前对话', hint: '仅清空界面消息', icon: Delete, keywords: 'clear 清空', run: clearChat },
    { id: 'act-upload', label: '上传文档…', hint: '跳转到文档管理', icon: Document, keywords: 'upload 上传', run: () => navigate('/documents') },
    { id: 'act-refresh', label: '刷新知识库列表', hint: '重新拉取 /api/kbs', icon: Refresh, keywords: 'refresh 刷新', run: () => { load(true); store.pushInfo('知识库列表已刷新') } },
    { id: 'act-logout', label: '退出登录', hint: '清除本地 Token 与会话', icon: SwitchButton, keywords: 'logout 退出', run: () => { store.logout(); router.push('/') } },
  ]

  const kbActions = kbs.value.map((kb) => ({
    id: `kb-${kb.kb_id}`,
    label: `切换到「${kb.name}」`,
    hint: kb.description || (kb.scope === 'shared' ? '共享知识库' : '私有知识库'),
    icon: FolderOpened,
    keywords: `kb ${kb.kb_id} ${kb.name}`,
    run: () => {
      store.setKbId(kb.kb_id)
      router.push('/')
      store.pushInfo(`已切换到「${kb.name}」`)
    },
  }))

  const appearance = [
    { id: 'theme-light', label: '浅色外观', icon: Sunny, keywords: 'light theme 浅色', run: () => setThemeMode('light') },
    { id: 'theme-dark', label: '深色外观', icon: Moon, keywords: 'dark theme 深色', run: () => setThemeMode('dark') },
    { id: 'theme-system', label: '跟随系统外观', icon: Monitor, keywords: 'system theme 跟随', run: () => setThemeMode('system') },
    ...ACCENT_OPTIONS.map((a) => ({
      id: `accent-${a.id}`,
      label: `强调色：${a.label}`,
      icon: Brush,
      keywords: `accent color ${a.id}`,
      run: () => {
        setAccent(a.id)
        store.pushInfo(`强调色已切换为「${a.label}」`)
      },
    })),
  ]

  const help = [
    { id: 'help-keys', label: '键盘快捷键', hint: '查看全部快捷操作', icon: QuestionFilled, keywords: 'shortcut keyboard 快捷键', run: () => openShortcuts() },
  ]

  const all = [
    { title: '导航', items: nav },
    { title: '操作', items: actions },
    { title: '知识库', items: kbActions },
    { title: '外观', items: appearance },
    { title: '帮助', items: help },
  ]

  const q = query.value.trim().toLowerCase()
  if (!q) return all.filter((g) => g.items.length)
  return all
    .map((g) => ({
      ...g,
      items: g.items.filter((it) => `${it.label} ${it.hint || ''} ${it.keywords || ''}`.toLowerCase().includes(q)),
    }))
    .filter((g) => g.items.length)
})

const flat = computed(() => groups.value.flatMap((g) => g.items))

function clampActive() {
  if (active.value > flat.value.length - 1) active.value = Math.max(flat.value.length - 1, 0)
}

watch(query, () => { active.value = 0 })

watch(commandOpen, (open) => {
  if (open) {
    query.value = ''
    active.value = 0
    load()
    nextTick(() => inputRef.value && inputRef.value.focus())
  }
})

function run(item) {
  if (!item) return
  closeCommand()
  // 关闭动画期间执行，避免面板遮挡反馈
  setTimeout(() => item.run && item.run(), 60)
}

function onKeydown(e) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = flat.value.length ? (active.value + 1) % flat.value.length : 0
    scrollActiveIntoView()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = flat.value.length ? (active.value - 1 + flat.value.length) % flat.value.length : 0
    scrollActiveIntoView()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    run(flat.value[active.value])
  } else if (e.key === 'Escape') {
    e.preventDefault()
    closeCommand()
  }
}

function scrollActiveIntoView() {
  nextTick(() => {
    const el = listRef.value && listRef.value.querySelector('.cmd-item.is-active')
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' })
  })
}

function indexOf(item) {
  return flat.value.findIndex((x) => x.id === item.id)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="cmd">
      <div
        v-if="commandOpen"
        class="cmd-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        @click.self="closeCommand"
        @keydown="onKeydown"
      >
        <div class="cmd-panel">
          <div class="cmd-input">
            <el-icon><Search /></el-icon>
            <input
              ref="inputRef"
              v-model="query"
              type="text"
              placeholder="搜索页面、动作或知识库…"
              aria-label="搜索命令"
              autocomplete="off"
              spellcheck="false"
              @keydown.stop="onKeydown"
            />
            <span class="kbd">ESC</span>
          </div>

          <div ref="listRef" class="cmd-list" role="listbox" aria-label="命令列表">
            <template v-for="group in groups" :key="group.title">
              <p class="cmd-group">{{ group.title }}</p>
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                role="option"
                class="cmd-item"
                :class="{ 'is-active': indexOf(item) === active }"
                :aria-selected="indexOf(item) === active"
                @mouseenter="active = indexOf(item)"
                @keydown.enter.stop.prevent="run(item)"
                @click="run(item)"
              >
                <span class="cmd-icon"><el-icon><component :is="item.icon" /></el-icon></span>
                <span class="cmd-text">
                  <span class="cmd-label">{{ item.label }}</span>
                  <span v-if="item.hint" class="cmd-hint">{{ item.hint }}</span>
                </span>
                <el-icon v-if="indexOf(item) === active" class="cmd-enter"><MagicStick /></el-icon>
              </button>
            </template>

            <p v-if="!flat.length" class="cmd-empty">没有匹配的命令</p>
          </div>

          <div class="cmd-foot">
            <span><span class="kbd">↑</span><span class="kbd">↓</span> 选择</span>
            <span><span class="kbd">↵</span> 执行</span>
            <span><span class="kbd">esc</span> 关闭</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cmd-overlay {
  position: fixed;
  inset: 0;
  z-index: 2500;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 16px;
  background: rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
.cmd-panel {
  width: 100%;
  max-width: 620px;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 18px;
  border: 1px solid var(--hairline);
  background: var(--surface-translucent);
  backdrop-filter: saturate(180%) blur(30px);
  -webkit-backdrop-filter: saturate(180%) blur(30px);
  box-shadow: var(--shadow-3);
}
.cmd-input {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--hairline);
  color: var(--text-tertiary);
}
.cmd-input input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text);
  font-family: inherit;
  font-size: 15px;
}
.cmd-input input::placeholder { color: var(--text-quaternary); }

.cmd-list {
  overflow-y: auto;
  padding: 6px 8px 10px;
  scroll-behavior: smooth;
}
.cmd-group {
  margin: 10px 8px 4px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-quaternary);
}
.cmd-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.cmd-item.is-active { background: var(--accent); color: #fff; }
.cmd-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 7px;
  background: var(--fill-1);
  color: var(--text-secondary);
  font-size: 14px;
}
.cmd-item.is-active .cmd-icon { background: rgba(255, 255, 255, 0.2); color: #fff; }
.cmd-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.35; }
.cmd-label { font-size: 13.5px; font-weight: 500; }
.cmd-hint { font-size: 11.5px; color: var(--text-tertiary); }
.cmd-item.is-active .cmd-hint { color: rgba(255, 255, 255, 0.8); }
.cmd-enter { margin-left: auto; font-size: 14px; opacity: 0.85; }
.cmd-empty {
  margin: 0;
  padding: 28px 12px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
}
.cmd-foot {
  display: flex;
  gap: 14px;
  padding: 9px 16px;
  border-top: 1px solid var(--hairline);
  color: var(--text-tertiary);
  font-size: 11px;
  background: var(--fill-1);
}
.cmd-foot span { display: inline-flex; align-items: center; gap: 4px; }

.cmd-enter-active { transition: opacity 180ms var(--ease); }
.cmd-leave-active { transition: opacity 140ms var(--ease); }
.cmd-enter-from, .cmd-leave-to { opacity: 0; }
.cmd-enter-active .cmd-panel { animation: scale-in 220ms var(--ease) both; }

@media (max-width: 640px) {
  .cmd-overlay { padding: 7vh 10px 10px; }
  .cmd-foot { display: none; }
}
</style>
