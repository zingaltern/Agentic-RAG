// 展示层格式化纯函数单测（零新增依赖，node --test）。
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  fileKind, formatBytes, formatDateTime, formatDuration, formatPercent,
  formatRelative, initials, toNumber,
} from './format.js'

test('toNumber：非数字回落到默认值', () => {
  assert.equal(toNumber('12.5'), 12.5)
  assert.equal(toNumber(undefined, 7), 7)
  assert.equal(toNumber('abc'), 0)
  assert.equal(toNumber(NaN, 3), 3)
})

test('formatBytes：单位与精度', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1024), '1.0 KB')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(50 * 1024 * 1024), '50.0 MB')
})

test('formatDuration：毫秒/秒/分钟', () => {
  assert.equal(formatDuration(320), '320 ms')
  assert.equal(formatDuration(1500), '1.5 s')
  assert.equal(formatDuration(29000), '29 s')
  assert.equal(formatDuration(125000), '2 分 5 秒')
})

test('formatPercent：0~1 转百分比', () => {
  assert.equal(formatPercent(0), '0%')
  assert.equal(formatPercent(0.6), '60%')
  assert.equal(formatPercent(0.1234, 1), '12.3%')
  assert.equal(formatPercent(undefined), '0%')
})

test('formatDateTime：支持秒级时间戳与非法值', () => {
  const s = formatDateTime(1792412381)
  assert.match(s, /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/)
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatDateTime('not-a-date'), '—')
})

test('formatRelative：相对时间分档', () => {
  const now = Date.UTC(2026, 9, 19, 12, 0, 0)
  assert.equal(formatRelative(now - 10 * 1000, now), '刚刚')
  assert.equal(formatRelative(now - 5 * 60 * 1000, now), '5 分钟前')
  assert.equal(formatRelative(now - 3 * 3600 * 1000, now), '3 小时前')
  assert.equal(formatRelative(now - 30 * 3600 * 1000, now), '昨天')
  assert.equal(formatRelative(now - 3 * 86400 * 1000, now), '3 天前')
})

test('initials：中文取末字、英文取首字母', () => {
  assert.equal(initials('张三'), '三')
  assert.equal(initials('alice'), 'A')
  assert.equal(initials(''), '?')
})

test('fileKind：常见扩展名归类', () => {
  assert.equal(fileKind('报告.pdf'), 'pdf')
  assert.equal(fileKind('数据.XLSX'), 'sheet')
  assert.equal(fileKind('main.py'), 'code')
  assert.equal(fileKind('config.yaml'), 'config')
  assert.equal(fileKind('README'), 'text')
})

test('formatDateTime / formatRelative：0 视为未知，不渲染 1970', () => {
  assert.equal(formatDateTime(0), '—')
  assert.equal(formatDateTime('0'), '—')
  assert.equal(formatRelative(0), '—')
  assert.equal(formatRelative('0'), '—')
})
