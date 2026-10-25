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
