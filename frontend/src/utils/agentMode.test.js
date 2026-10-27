// Agent 模式可见性纯函数单测（node --test）。
import test from 'node:test'
import assert from 'node:assert/strict'

import { agentFallbackNotice, isGreetingFastPath } from './agentMode.js'

test('关闭态：Agent 请求返回 reflect → 提示未启用', () => {
  const result = { mode: 'reflect', termination_reason: 'answered' }
  assert.deepEqual(agentFallbackNotice(result, 'agentic'), {
    kind: 'disabled',
    text: '服务端未启用 Agent，本次按反思模式回答。',
  })
})

test('Agent 失败回退：fallback_reflect → 提示已回退', () => {
  const result = { mode: 'reflect', termination_reason: 'fallback_reflect' }
  assert.deepEqual(agentFallbackNotice(result, 'agentic'), {
    kind: 'fallback',
    text: 'Agent 执行失败，已回退到反思模式回答。',
  })
})

test('问候快路：Agent 选中的 reflect 是预期，不提示', () => {
  const result = {
    mode: 'reflect',
    termination_reason: 'answered',
    debug_trace: [{ iter: 0, fast_path: 'greeting' }],
  }
  assert.equal(isGreetingFastPath(result), true)
  assert.equal(agentFallbackNotice(result, 'agentic'), null)
})

test('非 Agent 请求 / Agent 成功 / 无结果：不提示', () => {
  assert.equal(agentFallbackNotice({ mode: 'reflect' }, 'auto'), null)
  assert.equal(agentFallbackNotice({ mode: 'reflect' }, 'reflect'), null)
  assert.equal(agentFallbackNotice({ mode: 'agentic' }, 'agentic'), null)
  assert.equal(agentFallbackNotice({}, 'agentic'), null)
  assert.equal(agentFallbackNotice(null, 'agentic'), null)
})
