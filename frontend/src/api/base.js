/**
 * 13.11.2：BASE_URL 单一常量文件 —— 后端地址策略唯一入口。
 * Web（dev/prod）同源走 ''（vite proxy / FastAPI 托管）；桌面端（W8）经 VITE_API_BASE 注入；
 * 设置页「连接外部后端」（localStorage external_base）优先覆盖（桌面经 userData IPC 持久化）。
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

export function getBaseUrl() {
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
