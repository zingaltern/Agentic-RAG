/**
 * 统一请求包装（§6.4 响应语义 {code, message, data}）。
 * V5.1：Authorization: Bearer <token>；40101 清除 token 并触发登录视图（不重复弹错）。
 * 网络/超时失败一律返回 {code:50001} 契约包不 throw（与后端失败语义一致），调用方无需 catch。
 * 文档原文一律不在此层拼 HTML（S3，由组件模板插值渲染）。
 */
import { getBaseUrl, getToken, clearToken } from './base'
import { useAppStore } from '../stores/app'

export const REQUEST_TIMEOUT_MS = 90000

const USER_FACING = {
  40101: '请先登录',
  50001: '无法连接服务，请确认应用已启动、网络正常',
  21002: '本次处理超时，请换个问法或稍后再试',
}

// 技术性原文（令牌/ trace_id / 抛异常细节等）一律不上界面 → 回落错误码映射
const TECHNICAL_MESSAGE = /Authorization|Bearer|Token 签名|Token 解析|Token 已过期|trace_id|JSON|undefined|Error/i

export function friendlyError(code, message) {
  if (message && typeof message === 'string' && message.trim()) {
    if (/String should have|Field required|Input should|Value error|validation error/i.test(message)) {
      return '输入有误，请检查内容后再试'
    }
    if (!TECHNICAL_MESSAGE.test(message)) return message
  }
  if (USER_FACING[code]) return USER_FACING[code]
  return '操作失败，请稍后重试'
}

export async function api(path, options = {}) {
  const store = useAppStore()
  const silent = !!options.silent   // 调用方自行处理错误语义时跳过全局错误弹条（如登录态探测）
  delete options.silent
  const traceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now())
  const headers = { 'X-Trace-Id': traceId }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let resp
  try {
    resp = await fetch(getBaseUrl() + path, { ...options, headers, signal: controller.signal })
  } catch (e) {
    clearTimeout(timer)
    if (!silent) {
      store.pushError(USER_FACING[50001])
    }
    console.error(`[rag-api] network error trace=${traceId}`, e)
    return { code: 50001, message: USER_FACING[50001], data: {}, trace_id: traceId }
  }
  clearTimeout(timer)
  let json = null
  try {
    json = await resp.json()
  } catch (e) {
    if (!silent) {
      store.pushError('服务响应异常，请重试')
    }
    console.error(`[rag-api] parse error trace=${traceId} http=${resp.status}`, e)
    return { code: 50001, message: '服务响应异常，请重试', data: {}, trace_id: traceId }
  }
  if (!silent && json && json.code !== 0) {
    if (json.code === 40101 && getToken()) {
      clearToken()
      store.clearAuth()
      store.pushError('登录已失效，请重新登录')
    } else if (json.code === 40101) {
      store.pushError(USER_FACING[40101])
    } else {
      store.pushError(friendlyError(json.code, json.message))
    }
    console.error(`[rag-api] error code=${json.code} trace=${traceId}`, json)
  }
  return { ...(json || {}), trace_id: resp.headers.get('X-Trace-Id') || traceId }
}
