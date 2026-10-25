// 问答请求生命周期：begin/abort/finish/isCurrent（模块级单例，用例按顺序执行）。
import test from 'node:test'
import assert from 'node:assert/strict'

import { abortQuery, beginQuery, finishQuery, hasInflight, isCurrent } from '../composables/useChatQuery.js'

test('beginQuery：开启后 isCurrent 为真、有在途请求', () => {
  const token = beginQuery()
  assert.equal(hasInflight(), true)
  assert.equal(isCurrent(token.generation), true)
  assert.equal(token.signal.aborted, false)
})

test('beginQuery 二次调用：旧 token 失效且被 abort，新 token 生效', () => {
  const first = beginQuery()
  const second = beginQuery()
  assert.equal(first.signal.aborted, true)
  assert.equal(isCurrent(first.generation), false)
  assert.equal(isCurrent(second.generation), true)
})

test('abortQuery：中断在途请求并推进 generation', () => {
  const token = beginQuery()
  assert.equal(abortQuery(), true)
  assert.equal(hasInflight(), false)
  assert.equal(isCurrent(token.generation), false)
  assert.equal(abortQuery(), false)   // 已无在途请求
})

test('finishQuery：只清理自己那一次，不影响后发请求', () => {
  const first = beginQuery()
  finishQuery(first)
  assert.equal(hasInflight(), false)
  assert.equal(isCurrent(first.generation), true)

  const second = beginQuery()
  const third = beginQuery()          // 取代 second
  finishQuery(second)                 // 迟到的收尾不应清掉 third
  assert.equal(hasInflight(), true)
  assert.equal(isCurrent(third.generation), true)
  abortQuery()
})
