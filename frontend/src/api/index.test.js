// 40101 双重语义回归：越权（会话有效）不得误登出；凭证失效才登出。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'

import { api, friendlyError } from './index.js'
import { getToken, setToken } from './base.js'
import { useAppStore } from '../stores/app.js'

function fakeStorage() {
  const data = new Map()
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  }
}

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

function setup() {
  globalThis.localStorage = fakeStorage()
  setToken('valid-token')
  setActivePinia(createPinia())
  const store = useAppStore()
  store.setAuth({ user_id: 'u1', username: 'qauser', name: '验收用户', role: 'user' })
  return store
}

test('越权 40101（会话有效）不登出，仅提示权限错误', async () => {
  const store = setup()
  let meCalls = 0
  globalThis.fetch = async (url) => {
    const u = String(url)
    if (u.endsWith('/api/query')) return jsonResponse({ code: 40101, message: "无权访问知识库: 'qa_private'" })
    if (u.endsWith('/api/auth/me')) {
      meCalls += 1
      return jsonResponse({ code: 0, data: { user: { username: 'qauser' } } })
    }
    throw new Error('unexpected ' + u)
  }
  const res = await api('/api/query', { method: 'POST', body: '{}', silent: true })
  assert.equal(res.code, 40101)
  assert.equal(meCalls, 1)
  assert.equal(store.authenticated, true)
  assert.equal(getToken(), 'valid-token')
})

test('凭证失效 40101 会登出并清 Token', async () => {
  const store = setup()
  globalThis.fetch = async (url) => {
    const u = String(url)
    if (u.endsWith('/api/auth/me')) return jsonResponse({ code: 40101, message: '未登录' })
    return jsonResponse({ code: 40101, message: "无权访问知识库: 'qa_private'" })
  }
  await api('/api/query', { method: 'POST', body: '{}', silent: true })
  assert.equal(store.authenticated, false)
  assert.equal(getToken(), '')
})

test('/api/auth/me 自身的 40101 直接登出，不额外探活', async () => {
  const store = setup()
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return jsonResponse({ code: 40101, message: '未登录' })
  }
  await api('/api/auth/me', { silent: true })
  assert.equal(calls, 1)
  assert.equal(store.authenticated, false)
  assert.equal(getToken(), '')
})


test('friendlyError：30001 技术原文不上界面，但必须给出可行动的配置提示', () => {
  const text = friendlyError(30001, 'DeepSeek 调用失败: Error code: 401 - Authentication Fails')
  assert.match(text, /RAG_DEEPSEEK_API_KEY/)
  assert.doesNotMatch(text, /401|Authentication/)
})
