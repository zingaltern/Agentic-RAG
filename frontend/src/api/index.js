/**
 * 统一请求包装（§6.4 响应语义 {code, message, data}）。
 * - 非 0 code 全局错误提示条（store.pushError）；
 * - 回传 X-Trace-Id（S6 审计/前端 trace_id 面板展示）。
 * 文档原文一律不在此层拼 HTML（S3，由组件模板插值渲染）。
 */
import { BASE_URL, getApiKey, getBaseUrl } from './base'
import { useAppStore } from '../stores/app'

export async function api(path, options = {}) {
  const store = useAppStore()
  const traceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now())
  const headers = { 'X-Trace-Id': traceId }
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey           // S1：认证启用时携带
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  let resp
  try {
    resp = await fetch(getBaseUrl() + path, { ...options, headers })
  } catch (e) {
    store.pushError('网络请求失败: ' + e.message)
    throw e
  }
  let json = null
  try {
    json = await resp.json()
  } catch (e) {
    store.pushError(`响应解析失败（HTTP ${resp.status}）`)
    return { code: 50001, message: '响应解析失败', data: {}, trace_id: traceId }
  }
  if (json && json.code !== 0) {
    if (json.code === 40101) {
      store.pushError('[40101] 未认证或 API Key 无效——请在「设置总览」填写 API Key')
    } else {
      store.pushError(`[错误码 ${json.code}] ${json.message}`)
    }
  }
  return { ...(json || {}), trace_id: resp.headers.get('X-Trace-Id') || traceId }
}

export function queryUrl(query, { kbId = null, debug = false } = {}) {
  const params = new URLSearchParams()
  if (kbId) params.set('kb_id', kbId)
  if (debug) params.set('debug', 'true')
  const qs = params.toString()
  return `/api/query${qs ? '?' + qs : ''}`
}
