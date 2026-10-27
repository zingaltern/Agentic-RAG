/**
 * 统一请求包装（§6.4 响应语义 {code, message, data}）。
 * V5.1：Authorization: Bearer <token>；40101 可能是“凭证失效”也可能是“越权访问知识库”，
 * 先探活 /api/auth/me 区分：仅凭证确实失效才清除 token 并回登录页。
 * 网络/超时失败一律返回 {code:50001} 契约包不 throw（与后端失败语义一致），调用方无需 catch。
 * 文档原文一律不在此层拼 HTML（S3，由组件模板插值渲染）。
 *
 * 新增（可取消请求）：
 *   api(path, { signal }) —— 调用方传入 AbortSignal（如「停止生成」按钮），
 *   用户主动取消返回 {code:49901}，不弹全局错误；超时仍返回 50001。
 */
import { getBaseUrl, getToken } from './base.js'
import { useAppStore } from '../stores/app.js'

export const REQUEST_TIMEOUT_MS = 90000
export const CANCELLED_CODE = 49901

const USER_FACING = {
  40101: '请先登录',
  50001: '无法连接服务，请确认应用已启动、网络正常',
  21002: '本次处理超时，请换个问法或稍后再试',
  30001: '模型服务调用失败：请检查服务端 RAG_DEEPSEEK_API_KEY、余额与网络后重试',
  49901: '已取消本次请求',
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

/**
 * 40101 同时表示“未登录/凭证失效”和“越权访问知识库”（DEVELOPMENT 2.2）。
 * 仅凭错误码无法区分，故用 /api/auth/me 探活一次：
 * - true  → 会话仍有效，属于越权，不能登出；
 * - false → 凭证确实失效，登出；
 * - null  → 探活网络异常，保守返回 null，避免把用户误踢下线。
 * 并发请求共享同一个探活 Promise，避免 401 风暴放大成多次 /me。
 */
let sessionProbe = null
function probeSession(token) {
  if (sessionProbe) return sessionProbe
  sessionProbe = (async () => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort('timeout'), 5000)
    try {
      const resp = await fetch(getBaseUrl() + '/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
      const body = await resp.json()
      return Boolean(body && body.code === 0)
    } catch {
      return null
    } finally {
      clearTimeout(timer)
      sessionProbe = null
    }
  })()
  return sessionProbe
}

export async function api(path, options = {}) {
  const store = useAppStore()
  const {
    silent = false,        // 调用方自行处理错误语义时跳过全局错误弹条（如登录态探测）
    timeout = REQUEST_TIMEOUT_MS,
    signal: externalSignal,
    ...rest
  } = options
  const traceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now())
  const headers = { 'X-Trace-Id': traceId }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (rest.body && !(rest.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  // 内部控制器统一承载「超时」与「外部取消」两个来源
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort('timeout'), timeout)
  let relayAbort = null
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort('cancelled')
    } else {
      relayAbort = () => controller.abort('cancelled')
      externalSignal.addEventListener('abort', relayAbort, { once: true })
    }
  }

  let resp
  try {
    resp = await fetch(getBaseUrl() + path, { ...rest, headers, signal: controller.signal })
  } catch (e) {
    const cancelled = Boolean(externalSignal && externalSignal.aborted)
    if (cancelled) {
      // 用户主动取消：静默返回，不写 error 日志（不算故障）
      return { code: CANCELLED_CODE, message: USER_FACING[CANCELLED_CODE], data: {}, trace_id: traceId }
    }
    if (!silent) store.pushError(USER_FACING[50001])
    console.error(`[rag-api] network error trace=${traceId}`, e)
    return { code: 50001, message: USER_FACING[50001], data: {}, trace_id: traceId }
  } finally {
    clearTimeout(timer)
    if (externalSignal && relayAbort) externalSignal.removeEventListener('abort', relayAbort)
  }

  let json = null
  try {
    json = await resp.json()
  } catch (e) {
    if (!silent) store.pushError('服务响应异常，请重试')
    console.error(`[rag-api] parse error trace=${traceId} http=${resp.status}`, e)
    return { code: 50001, message: '服务响应异常，请重试', data: {}, trace_id: traceId }
  }
  // 401 的「清理」不受 silent 影响：探测/后台轮询拿到的失效 Token 也必须清掉，
  // 否则界面会停在“看似已登录、实际所有请求都 401”的状态；silent 只抑制提示条。
  if (json && json.code === 40101) {
    const currentToken = getToken()
    if (!currentToken) {
      if (!silent) store.pushError(USER_FACING[40101])
    } else {
      // /api/auth/me 的 40101 即“会话已失效”；其他接口先探活，区分“越权”与“登录失效”，
      // 否则普通用户访问无权知识库会被误登出。
      const sessionValid = path === '/api/auth/me' ? false : await probeSession(currentToken)
      if (sessionValid === false) {
        store.logout()
        if (!silent) store.pushError('登录已失效，请重新登录')
      } else if (!silent) {
        store.pushError(friendlyError(json.code, json.message))
      }
    }
    if (!silent) console.error(`[rag-api] 40101 trace=${traceId}`, json)
  } else if (!silent && json && json.code !== 0) {
    store.pushError(friendlyError(json.code, json.message))
    console.error(`[rag-api] error code=${json.code} trace=${traceId}`, json)
  }
  return { ...(json || {}), trace_id: resp.headers.get('X-Trace-Id') || traceId }
}
