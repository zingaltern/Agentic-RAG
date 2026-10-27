// 回答元信息 / 反思过程的展示判定（纯函数，node --test 覆盖）。
// 约定：
// - 只有带来源或引用标号的回答才做“已证/待核实”判断；问候等无证据回答不显示幻觉徽标。
// - fast_path（如 greeting）不是反思步骤，不进入“反思过程（N 步）”。
// - 反思轮次为 0 时也要显示“反思 0 轮”，不要用 truthy 判断吞掉零。

export function hasEvidence(result) {
  if (!result) return false
  const sources = Array.isArray(result.sources) ? result.sources : []
  const used = result.citations && result.citations.used
  const citations = Array.isArray(used) ? used : (used ? [used] : [])
  return sources.length > 0 || citations.length > 0
}

/** 反思面板步骤：排除 greeting 等快捷通道，避免被误读为“反思 1 步”。 */
export function reflectionSteps(result) {
  const trace = Array.isArray(result && result.debug_trace) ? result.debug_trace : []
  return trace.filter((step) => step && !step.fast_path)
}

/** 轮次标签；iterations 为 0 也返回“反思 0 轮”，undefined/null 返回空串。 */
export function reflectionRoundsLabel(result) {
  const n = result && result.iterations
  if (typeof n !== 'number' || !Number.isFinite(n)) return ''
  return `反思 ${n} 轮`
}

/** 运行模式标签；fallback_reflect 要明确提示“Agent 失败后回退”，不能静默。 */
export function modeLabel(result) {
  if (!result) return ''
  if (result.termination_reason === 'fallback_reflect') return 'Agent 失败→反思'
  const mode = result.mode
  if (mode === 'agentic') return 'Agent'
  if (mode === 'auto') return '自动'
  if (mode === 'reflect') return '反思'
  return ''
}

/** 非正常终止原因；answered 不显示，避免每条成功回答都挂一个“完成”标签。 */
export function terminationLabel(result) {
  const reason = result && result.termination_reason
  if (!reason || reason === 'answered') return ''
  const labels = {
    clarify: '需澄清',
    abstain_insufficient: '证据不足',
    budget_steps: '步数预算',
    budget_time: '时间预算',
    fallback_reflect: '已回退反思',
    policy_blocked: '策略拦截',
    plan_invalid: '计划无效',
    tool_error: '工具失败',
    protocol_error: '协议错误',
    error: '执行错误',
  }
  return labels[reason] || `终止：${reason}`
}

/** 步数标签：Agent 用 agent_steps，reflect 才用 iterations（反思轮次）。 */
export function stepLabel(result) {
  if (!result) return ''
  if (result.mode === 'agentic') {
    const steps = Number(result.agent_steps)
    if (Number.isFinite(steps) && steps >= 0) return `Agent ${steps} 步`
  }
  return reflectionRoundsLabel(result)
}

/** 正常回答：有证据（或索引告警）才显示元信息条；问候不显示。 */
export function shouldShowAnswerMeta(result) {
  if (!result) return false
  return hasEvidence(result) || Boolean(result.index_dna_mismatch)
}

/**
 * 执行过程面板标题：reflect 的 debug_trace 同时含检索与检测步骤，不能整体叫“反思步”；
 * Agent 运行则明确为“Agent 执行过程”，避免与“反思 0 轮”自相矛盾。
 */
export function tracePanelTitle(result, count) {
  const n = Number(count) || 0
  if (result && result.mode === 'agentic') return `Agent 执行过程（${n} 步）`
  return `执行过程（${n} 步）`
}

/** 单条 trace 的准确标题（检索/检测/计划/工具/反思分开命名）。 */
export function traceStepLabel(step, fallbackIndex = 0) {
  if (!step || typeof step !== 'object') return `第 ${fallbackIndex + 1} 步`
  const iter = Number.isFinite(Number(step.iter)) ? Number(step.iter) : fallbackIndex
  if (step.action === 'plan') return '计划'
  if (step.action === 'reflect') {
    return `反思第 ${iter} 轮${step.decision ? ` · ${step.decision}` : ''}`
  }
  if (step.action) {
    // Agent 工具步骤（kb_search / kb_fetch / reflect 等）
    return `第 ${iter} 步 · ${step.action}`
  }
  if (step.detection) return '幻觉检测'
  if (step.query) return `检索第 ${iter} 轮`
  return `第 ${iter} 步`
}

/** 子目标证据说明：合成目标自身不持证据，但要告诉用户“由哪些依赖目标支持”。 */
export function goalEvidenceText(goal) {
  if (!goal) return ''
  const ids = Array.isArray(goal.evidence_ids) ? goal.evidence_ids : []
  if (ids.length) return `证据：${ids.join(' / ')}`
  const deps = Array.isArray(goal.depends_on) ? goal.depends_on : []
  if (deps.length) return `证据：由 ${deps.join(' / ')} 支持（合成目标不单独持有证据）`
  return ''
}
