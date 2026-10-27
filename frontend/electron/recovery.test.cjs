/**
 * 外部后端不可达恢复对话框纯逻辑单测（node:test）。
 * 运行：cd frontend && npm run test:desktop
 */
'use strict'

const test = require('node:test')
const assert = require('node:assert')
const recovery = require('./recovery.cjs')

test('外部后端不可达对话框：文案/按钮/默认项可操作', () => {
  const spec = recovery.buildExternalUnavailableDialog('http://127.0.0.1:9', 60000)
  assert.strictEqual(spec.type, 'error')
  assert.strictEqual(spec.title, '外部后端不可达')
  assert.match(spec.message, /无法连接外部后端/)
  assert.match(spec.detail, /60s 内 \/api\/health 未返回 ok/)
  assert.match(spec.detail, /http:\/\/127\.0\.0\.1:9/)
  assert.match(spec.detail, /使用本机服务/)
  assert.deepStrictEqual(spec.buttons, ['使用本机服务', '重试', '退出'])
  assert.strictEqual(spec.defaultId, 0, '默认必须是把用户救回本机服务')
  assert.strictEqual(spec.cancelId, 2, '关闭对话框必须等价退出')
  assert.strictEqual(spec.noLink, true)
  assert.ok(!spec.detail.includes('设置页'), '外部模式没有窗口可进设置页，文案不能再指向它')
})

test('外部后端不可达对话框：超时秒数按传入值展示', () => {
  const spec = recovery.buildExternalUnavailableDialog('https://kb.example.com', 30000)
  assert.match(spec.detail, /30s 内/)
  assert.match(spec.detail, /https:\/\/kb\.example\.com/)
})

test('恢复动作映射：0 本机 / 1 重试 / 2 或未知退出', () => {
  assert.strictEqual(recovery.resolveRecoveryAction(0), recovery.ACTIONS.USE_LOCAL)
  assert.strictEqual(recovery.resolveRecoveryAction(1), recovery.ACTIONS.RETRY)
  assert.strictEqual(recovery.resolveRecoveryAction(2), recovery.ACTIONS.QUIT)
  assert.strictEqual(recovery.resolveRecoveryAction(undefined), recovery.ACTIONS.QUIT)
  assert.strictEqual(recovery.resolveRecoveryAction(99), recovery.ACTIONS.QUIT)
  assert.strictEqual(recovery.resolveRecoveryAction('1'), recovery.ACTIONS.RETRY)
})
