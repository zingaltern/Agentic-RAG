// 前端最小单测（P1，零新增依赖）：BASE_URL/Token 存取与 localStorage 降级。
import test from 'node:test'
import assert from 'node:assert/strict'

import { BASE_URL, clearToken, getBaseUrl, getToken, setToken } from './base.js'

function fakeStorage() {
  const data = new Map()
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  }
}

test('BASE_URL defaults to empty string without Vite env', () => {
  assert.equal(BASE_URL, '')
})

test('token round-trip and clear', () => {
  globalThis.localStorage = fakeStorage()
  clearToken()
  assert.equal(getToken(), '')
  setToken('abc')
  assert.equal(getToken(), 'abc')
  clearToken()
  assert.equal(getToken(), '')
})

test('external base overrides BASE_URL', () => {
  globalThis.localStorage = fakeStorage()
  assert.equal(getBaseUrl(), BASE_URL)
  globalThis.localStorage.setItem('external_base', 'http://127.0.0.1:9000')
  assert.equal(getBaseUrl(), 'http://127.0.0.1:9000')
})

test('storage errors degrade safely', () => {
  globalThis.localStorage = {
    getItem: () => { throw new Error('denied') },
    setItem: () => { throw new Error('denied') },
    removeItem: () => { throw new Error('denied') },
  }
  assert.equal(getBaseUrl(), BASE_URL)
  assert.equal(getToken(), '')
  setToken('x')
  clearToken()
})

test('desktop shell ignores localStorage external_base and stays same-origin', () => {
  globalThis.localStorage = fakeStorage()
  globalThis.localStorage.setItem('external_base', 'http://127.0.0.1:9')
  globalThis.window = { desktop: { getExternalBase: async () => 'http://127.0.0.1:8001' } }
  try {
    assert.equal(getBaseUrl(), '', '桌面窗口由主进程加载目标地址，API 必须同源')
  } finally {
    delete globalThis.window
  }
})

test('desktop shell does not read localStorage at all', () => {
  let reads = 0
  globalThis.localStorage = {
    getItem: () => { reads += 1; throw new Error('denied') },
    setItem: () => {},
    removeItem: () => {},
  }
  globalThis.window = { desktop: { getExternalBase: () => Promise.resolve('') } }
  try {
    assert.equal(getBaseUrl(), '')
    assert.equal(reads, 0, '桌面端不应再访问 localStorage.external_base')
  } finally {
    delete globalThis.window
  }
})
