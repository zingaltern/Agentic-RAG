// 密码策略单测：与后端口径保持一致（≥8 位且含字母+数字）。
import test from 'node:test'
import assert from 'node:assert/strict'

import { PASSWORD_HINT, PASSWORD_RULE, validatePassword } from './password.js'

test('validatePassword：长度不足', () => {
  assert.equal(validatePassword('a1b2c3'), '密码至少 8 位')
  assert.equal(validatePassword(''), '密码至少 8 位')
  assert.equal(validatePassword(null), '密码至少 8 位')
})

test('validatePassword：纯字母/纯数字不通过', () => {
  assert.equal(validatePassword('abcdefgh'), '密码需同时包含字母和数字')
  assert.equal(validatePassword('12345678'), '密码需同时包含字母和数字')
})

test('validatePassword：合规密码通过', () => {
  assert.equal(validatePassword('abcd1234'), '')
  assert.equal(validatePassword('Passw0rd!'), '')
})

test('PASSWORD_RULE 与 validatePassword 口径一致', () => {
  for (const pw of ['abcd1234', 'Passw0rd!', 'a1234567']) {
    assert.equal(PASSWORD_RULE.test(pw), true, pw)
  }
  for (const pw of ['abcdefgh', '12345678', 'a1b2']) {
    assert.equal(PASSWORD_RULE.test(pw), false, pw)
  }
  assert.match(PASSWORD_HINT, /8/)
})
