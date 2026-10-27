// Agent 模式可见性判定（纯函数，node --test 覆盖）。
//
// 背景（P6 D01）：agent.enabled=false 时服务端把 mode=agentic 直通 reflect，
// 但前端只保留本地“Agent”选中态，用户不知道 Agent 没生效。这里统一生成提示，
// 区分三种情况：
// - 服务端未开启：reflect + 非问候快路（termination_reason 通常为 answered）；
// - Agent 已尝试但失败回退：termination_reason=fallback_reflect；
// - 问候快路（L0）：即使选了 Agent 也走 reflect，这是预期行为，不提示“未开启”。

/** @param {object|null} result 响应 data（含 mode/termination_reason/debug_trace） */
export function isGreetingFastPath(result) {
  const trace = Array.isArray(result && result.debug_trace) ? result.debug_trace : []
  return trace.some((step) => step && step.fast_path === 'greeting')
}

/**
 * 请求模式为 Agent 但实际回答用 reflect 时，返回可见提示；否则 null。
 * @param {object|null} result
 * @param {string} requestedMode 发起请求时前端选中的模式（'reflect'|'auto'|'agentic'）
 */
export function agentFallbackNotice(result, requestedMode) {
  if (requestedMode !== 'agentic') return null
  if (!result || result.mode !== 'reflect') return null
  if (isGreetingFastPath(result)) return null
  if (result.termination_reason === 'fallback_reflect') {
    return {
      kind: 'fallback',
      text: 'Agent 执行失败，已回退到反思模式回答。',
    }
  }
  return {
    kind: 'disabled',
    text: '服务端未启用 Agent，本次按反思模式回答。',
  }
}
