// 来源链接安全化：只放行 http/https（防止 javascript:/data: 注入到 <a href>）。
import test from 'node:test'
import assert from 'node:assert/strict'

import { hostOf, safeHttpUrl } from './url.js'

test('safeHttpUrl：放行 http/https', () => {
  assert.equal(safeHttpUrl('https://example.com/a?b=1'), 'https://example.com/a?b=1')
  assert.equal(safeHttpUrl('http://127.0.0.1:8000/doc'), 'http://127.0.0.1:8000/doc')
})

test('safeHttpUrl：拒绝危险或非法 scheme', () => {
  assert.equal(safeHttpUrl('javascript:alert(1)'), '')
  assert.equal(safeHttpUrl('data:text/html,<script>1</script>'), '')
  assert.equal(safeHttpUrl('file:///etc/passwd'), '')
  assert.equal(safeHttpUrl(''), '')
  assert.equal(safeHttpUrl(null), '')
  assert.equal(safeHttpUrl('not a url'), '')
})

test('hostOf：提取域名，非法返回空串', () => {
  assert.equal(hostOf('https://example.com/a'), 'example.com')
  assert.equal(hostOf('javascript:alert(1)'), '')
  assert.equal(hostOf(null), '')
})
