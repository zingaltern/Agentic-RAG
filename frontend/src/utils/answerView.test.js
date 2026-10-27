// 回答视图判定纯函数单测（node --test）。
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  goalEvidenceText, hasEvidence, modeLabel, reflectionRoundsLabel, reflectionSteps,
  shouldShowAnswerMeta, stepLabel, terminationLabel, tracePanelTitle, traceStepLabel,
} from './answerView.js'

test('hasEvidence：来源或引用任一存在即为真', () => {
  assert.equal(hasEvidence(null), false)
  assert.equal(hasEvidence({ sources: [], citations: { used: [] } }), false)
  assert.equal(hasEvidence({ sources: [{ filename: '员工手册.md' }], citations: { used: [] } }), true)
  assert.equal(hasEvidence({ sources: [], citations: { used: ['[1]'] } }), true)
  assert.equal(hasEvidence({ sources: [], citations: { used: '[1]' } }), true)
})

test('reflectionSteps：过滤 greeting 的 fast_path', () => {
  assert.deepEqual(reflectionSteps({ debug_trace: [{ iter: 0, fast_path: 'greeting' }] }), [])
  const steps = reflectionSteps({
    debug_trace: [{ iter: 0, fast_path: 'greeting' }, { iter: 0, query: 'q', evaluator_score: 0.9 }],
  })
  assert.equal(steps.length, 1)
  assert.equal(steps[0].query, 'q')
  assert.deepEqual(reflectionSteps({}), [])
})

test('reflectionRoundsLabel：0 轮也要显示，缺省不显示', () => {
  assert.equal(reflectionRoundsLabel({ iterations: 0 }), '反思 0 轮')
  assert.equal(reflectionRoundsLabel({ iterations: 2 }), '反思 2 轮')
  assert.equal(reflectionRoundsLabel({}), '')
  assert.equal(reflectionRoundsLabel({ iterations: '2' }), '')
})

test('shouldShowAnswerMeta：问候无证据不显示，错误/索引告警另行处理', () => {
  assert.equal(shouldShowAnswerMeta({ sources: [], citations: { used: [] } }), false)
  assert.equal(shouldShowAnswerMeta({ sources: [{ filename: 'x' }] }), true)
  assert.equal(shouldShowAnswerMeta({ sources: [], index_dna_mismatch: true }), true)
  assert.equal(shouldShowAnswerMeta(null), false)
})


test('modeLabel：Agent / 反思 / 回退明确区分', () => {
  assert.equal(modeLabel({ mode: 'agentic' }), 'Agent')
  assert.equal(modeLabel({ mode: 'reflect' }), '反思')
  assert.equal(modeLabel({ mode: 'reflect', termination_reason: 'fallback_reflect' }), 'Agent 失败→反思')
  assert.equal(modeLabel(null), '')
})

test('terminationLabel：正常完成不显示，失败原因中文化', () => {
  assert.equal(terminationLabel({ termination_reason: 'answered' }), '')
  assert.equal(terminationLabel({}), '')
  assert.equal(terminationLabel({ termination_reason: 'abstain_insufficient' }), '证据不足')
  assert.equal(terminationLabel({ termination_reason: 'budget_time' }), '时间预算')
  assert.equal(terminationLabel({ termination_reason: 'fallback_reflect' }), '已回退反思')
  assert.equal(terminationLabel({ termination_reason: 'unknown_x' }), '终止：unknown_x')
})

test('stepLabel：Agent 步骤不能被标成反思轮次', () => {
  assert.equal(stepLabel({ mode: 'agentic', agent_steps: 2, iterations: 2 }), 'Agent 2 步')
  assert.equal(stepLabel({ mode: 'agentic', agent_steps: 0, iterations: 0 }), 'Agent 0 步')
  assert.equal(stepLabel({ mode: 'reflect', iterations: 2 }), '反思 2 轮')
  assert.equal(stepLabel({ mode: 'reflect', iterations: 0 }), '反思 0 轮')
  assert.equal(stepLabel({}), '')
})

test('D01 回归：0 轮 + 有证据仍显示元信息条与“反思 0 轮”', () => {
  const result = {
    iterations: 0,
    mode: 'reflect',
    sources: [{ filename: '员工手册.md' }],
    citations: { used: ['[1]'], confirmed: 0 },
    elapsed_ms: 12,
  }
  assert.equal(shouldShowAnswerMeta(result), true)
  assert.equal(reflectionRoundsLabel(result), '反思 0 轮')
  assert.equal(stepLabel(result), '反思 0 轮')
})

test('tracePanelTitle：reflect 不叫“反思步”，Agent 标 Agent 执行过程', () => {
  assert.equal(tracePanelTitle({ mode: 'reflect' }, 2), '执行过程（2 步）')
  assert.equal(tracePanelTitle({ mode: 'agentic' }, 4), 'Agent 执行过程（4 步）')
  assert.equal(tracePanelTitle(null, 0), '执行过程（0 步）')
})

test('traceStepLabel：检索/检测/计划/工具分开命名，不再统一“第 N 轮”', () => {
  assert.equal(traceStepLabel({ query: 'q', iter: 0 }, 0), '检索第 0 轮')
  assert.equal(traceStepLabel({ detection: { verified: true }, iter: 0 }, 1), '幻觉检测')
  assert.equal(traceStepLabel({ action: 'plan', iter: 0 }, 0), '计划')
  assert.equal(traceStepLabel({ action: 'kb_search', iter: 2 }, 0), '第 2 步 · kb_search')
  assert.equal(traceStepLabel({ action: 'reflect', iter: 1, decision: 'answer' }, 0), '反思第 1 轮 · answer')
})

test('goalEvidenceText：合成目标没有自有证据时说明依赖来源', () => {
  assert.equal(goalEvidenceText({ evidence_ids: ['E1', 'E2'] }), '证据：E1 / E2')
  assert.equal(goalEvidenceText({ evidence_ids: [], depends_on: ['g1', 'g2'] }),
    '证据：由 g1 / g2 支持（合成目标不单独持有证据）')
  assert.equal(goalEvidenceText({ evidence_ids: [] }), '')
})
