/**
 * 外观控制（Apple 式 Appearance）：亮/暗/跟随系统 + 四套强调色。
 * 状态放在模块级 ref（单例），侧栏按钮与设置页双向同步；
 * 写入 <html data-theme / data-accent> 与 localStorage，刷新后保持。
 */
import { ref } from 'vue'

const THEME_KEY = 'rag_theme_mode'
const ACCENT_KEY = 'rag_accent'

export const ACCENT_OPTIONS = [
  { id: 'coral', label: '珊瑚橙', color: '#f5632f' },
  { id: 'blue', label: '海湾蓝', color: '#0a6cff' },
  { id: 'violet', label: '鸢尾紫', color: '#6f4bf2' },
  { id: 'green', label: '松针绿', color: '#17864b' },
]

export const THEME_OPTIONS = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
]

const hasDOM = typeof document !== 'undefined'
const hasWindow = typeof window !== 'undefined'

function read(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback
  } catch {
    return fallback
  }
}
function write(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch { /* ignore */ }
}

const mql = hasWindow && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null

export const themeMode = ref(read(THEME_KEY, 'system'))
export const accent = ref(read(ACCENT_KEY, 'coral'))
export const resolvedTheme = ref('light')

function resolve(mode) {
  if (mode === 'system') return mql && mql.matches ? 'dark' : 'light'
  return mode === 'dark' ? 'dark' : 'light'
}

/** 应用主题到 <html>；Element Plus 暗色包靠 .dark 类生效。 */
export function applyTheme() {
  const resolved = resolve(themeMode.value)
  resolvedTheme.value = resolved
  if (!hasDOM) return
  const root = document.documentElement
  root.dataset.theme = resolved
  root.dataset.accent = accent.value
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', resolved === 'dark' ? '#161618' : '#f5f5f7')
}

export function setThemeMode(mode) {
  themeMode.value = ['system', 'light', 'dark'].includes(mode) ? mode : 'system'
  write(THEME_KEY, themeMode.value)
  applyTheme()
}

export function setAccent(id) {
  accent.value = ACCENT_OPTIONS.some((a) => a.id === id) ? id : 'coral'
  write(ACCENT_KEY, accent.value)
  applyTheme()
}

/** 侧栏快捷键：亮 → 暗 → 系统循环。 */
export function cycleTheme() {
  const order = ['light', 'dark', 'system']
  const next = order[(order.indexOf(themeMode.value) + 1) % order.length]
  setThemeMode(next)
  return next
}

/** 首次挂载调用一次：写入初值并跟随系统切换。 */
export function initTheme() {
  applyTheme()
  if (mql && mql.addEventListener) {
    mql.addEventListener('change', () => {
      if (themeMode.value === 'system') applyTheme()
    })
  }
}
