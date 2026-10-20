// 会话历史构造：轮次截取、失败回答过滤、重生成时排除当前问题与未来对话。
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildHistory, findLastUserIndex } from './chat.js'

const user = (text) => ({ role: 'user', text })
const okAnswer = (text) => ({ role: 'assistant', text, result: { code: 0 } })
const badAnswer = (text) => ({ role: 'assistant', text, result: { code: 21002 } })

test('buildHistory：过滤失败回答，只保留成功对话', () => {
  const history = buildHistory([user('q1'), badAnswer('boom'), user('q2'), okAnswer('a2')])
  assert.deepEqual(history.map((h) => h.content), ['q1', 'q2', 'a2'])
})

test('buildHistory：最多 6 轮（12 条）', () => {
  const messages = []
  for (let i = 0; i < 10; i += 1) {
    messages.push(user(`q${i}`), okAnswer(`a${i}`))
  }
  const history = buildHistory(messages)
  assert.equal(history.length, 12)
  assert.equal(history[0].content, 'q4')
  assert.equal(history[11].content, 'a9')
})

test('buildHistory：重生成排除当前问题与之后的对话', () => {
  const messages = [user('q1'), okAnswer('a1'), user('q2'), okAnswer('a2'), user('q3')]
  const history = buildHistory(messages, { beforeIndex: 3, excludeTrailingUser: true })
  assert.deepEqual(history.map((h) => h.content), ['q1', 'a1'])
})

test('buildHistory：没有 excludeTrailingUser 时保留末尾 user（普通提问路径）', () => {
  const messages = [user('q1'), okAnswer('a1'), user('q2')]
  const history = buildHistory(messages)
  assert.deepEqual(history.map((h) => h.content), ['q1', 'a1', 'q2'])
})

test('findLastUserIndex：返回最后一条 user 的下标', () => {
  assert.equal(findLastUserIndex([user('a'), okAnswer('b'), user('c'), okAnswer('d')]), 2)
  assert.equal(findLastUserIndex([okAnswer('a')]), -1)
  assert.equal(findLastUserIndex([]), -1)
  assert.equal(findLastUserIndex(null), -1)
})
