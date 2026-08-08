/**
 * 13.11.2：BASE_URL 单一常量文件 —— 后端地址策略唯一入口。
 * Web（dev/prod）同源走 ''（vite proxy / FastAPI 托管）；桌面端（W8）经 VITE_API_BASE 注入；
 * 设置页「连接外部后端」（localStorage external_base）优先覆盖（桌面经 userData IPC 持久化）。
 */
export const BASE_URL = import.meta.env.VITE_API_BASE || ''

export function getBaseUrl() {
  try {
    const external = localStorage.getItem('external_base')
    return external || BASE_URL
  } catch {
    return BASE_URL
  }
}

export function getApiKey() {
  try {
    return localStorage.getItem('api_key') || ''
  } catch {
    return ''
  }
}
