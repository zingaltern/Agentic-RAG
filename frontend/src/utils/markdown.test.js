// 轻量 Markdown 解析单测：保证「只产出 token、不产出 HTML」，且常见语法不丢内容。
import test from 'node:test'
import assert from 'node:assert/strict'

import { parseInline, parseMarkdown, toBlocks } from './markdown.js'

test('parseInline：粗体/行内代码/引用标号', () => {
  const parts = parseInline('这是**重点**和`code`，见[1]。')
  assert.deepEqual(parts.map((p) => p.type), ['text', 'strong', 'text', 'code', 'text', 'cite', 'text'])
  assert.equal(parts[1].text, '重点')
  assert.equal(parts[3].text, 'code')
  assert.equal(parts[5].text, '[1]')
})

test('parseInline：空文本返回空数组，不产生空 token', () => {
  assert.deepEqual(parseInline(''), [])
  assert.deepEqual(parseInline(null), [])
  assert.deepEqual(parseInline('纯文本'), [{ type: 'text', text: '纯文本' }])
})

test('parseMarkdown：标题 / 列表 / 引用 / 分隔线', () => {
  const blocks = parseMarkdown('## 年假\n\n- 满 1 年：5 天\n- 满 2 年：10 天\n\n> 需提前 3 个工作日\n\n---')
  assert.equal(blocks[0].type, 'h2')
  assert.equal(blocks[0].text, '年假')
  assert.equal(blocks[1].type, 'ul')
  assert.equal(blocks[1].items.length, 2)
  assert.equal(blocks[2].type, 'quote')
  assert.equal(blocks[3].type, 'hr')
})

test('parseMarkdown：有序列表与代码块', () => {
  const blocks = parseMarkdown('1. 第一步\n2. 第二步\n\n```python\nprint(1)\n```')
  assert.equal(blocks[0].type, 'ol')
  assert.deepEqual(blocks[0].items, ['第一步', '第二步'])
  assert.equal(blocks[1].type, 'code')
  assert.equal(blocks[1].lang, 'python')
  assert.equal(blocks[1].text, 'print(1)')
})

test('parseMarkdown：连续行合并为段落，空行分段', () => {
  const blocks = parseMarkdown('第一行\n第二行\n\n第二段')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[0].text, '第一行 第二行')
  assert.equal(blocks[1].text, '第二段')
})

test('parseMarkdown：未闭合代码围栏也能优雅结束', () => {
  const blocks = parseMarkdown('```\nunclosed')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].type, 'code')
  assert.equal(blocks[0].text, 'unclosed')
})

test('parseMarkdown：空输入与纯空白', () => {
  assert.deepEqual(parseMarkdown(''), [])
  assert.deepEqual(parseMarkdown('   \n\n  '), [])
  assert.deepEqual(toBlocks(undefined), [])
})
