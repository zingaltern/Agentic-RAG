/**
 * 13.11.2：BASE_URL 单一常量文件 —— 后端地址策略唯一入口。
 * Web（dev/prod）同源走 ''（vite proxy / FastAPI 托管）；桌面端（W8）窗口由主进程
 * 加载目标后端 URL（本机或外部），因此 /api 必须同源，不能再被 localStorage 的
 * external_base 覆盖（桌面端外部后端权威值在 userData，经 IPC 维护）。
 * V5.1：登录 Token（localStorage auth_token）持久化，API Key 机制已移除。
 */
// 静态访问 import.meta.env.VITE_API_BASE，保证 Vite 构建期能替换；
// try/catch 仅用于兼容 Node 单测环境（无 Vite 时 import.meta.env 不存在）。
let baseUrl = ''
try {
  baseUrl = import.meta.env.VITE_API_BASE || ''
} catch {
  baseUrl = ''
}
export const BASE_URL = baseUrl

/** 桌面壳：preload 暴露 window.desktop；此时窗口 origin 就是目标后端。 */
function isDesktopShell() {
  try {
    return typeof window !== 'undefined' && !!window.desktop && typeof window.desktop.getExternalBase === 'function'
  } catch {
    return false
  }
}

export function getBaseUrl() {
  // 桌面端不读 localStorage：主进程已经把窗口加载到本机/外部后端 URL，
  // 同源请求既能连本机也能连外部；旧 external_base 残留不得覆盖它（C15 D3）。
  if (isDesktopShell()) return ''
  try {
    const external = localStorage.getItem('external_base')
    return external || BASE_URL
  } catch {
    return BASE_URL
  }
}

export function getToken() {
  try {
    return localStorage.getItem('auth_token') || ''
  } catch {
    return ''
  }
}

export function setToken(token) {
  try {
    localStorage.setItem('auth_token', token || '')
  } catch { /* ignore */ }
}

export function clearToken() {
  try {
    localStorage.removeItem('auth_token')
  } catch { /* ignore */ }
}
