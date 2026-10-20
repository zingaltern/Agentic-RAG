/**
 * 会话历史构造（纯函数，便于单测）。
 * 规则：
 * - 只取成功回答；中断/失败的 assistant 不进上下文，避免污染模型；
 * - 最多最近 rounds 轮（1 轮 = 用户 + 助手，故按 2*rounds 条截取）；
 * - 重生成时传 beforeIndex + excludeTrailingUser，确保「当前问题」不会同时出现在
 *   payload.query 与 history 里，也不会带进该问题之后的"未来对话"。
 */

export function findLastUserIndex(messages) {
  const list = Array.isArray(messages) ? messages : []
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i] && list[i].role === 'user') return i
  }
  return -1
}

function isUsable(message) {
  if (!message) return false
  if (message.role === 'user') return true
  return Boolean(message.result && message.result.code === 0 && !message.interrupted)
}

export function buildHistory(messages, options = {}) {
  const { beforeIndex = null, excludeTrailingUser = false, rounds = 6 } = options
  const list = Array.isArray(messages) ? messages : []
  const end = beforeIndex == null ? list.length : Math.max(0, Math.min(beforeIndex, list.length))
  let slice = list.slice(0, end)

  if (excludeTrailingUser && slice.length && slice[slice.length - 1].role === 'user') {
    slice = slice.slice(0, -1)
  }

  return slice
    .filter(isUsable)
    .slice(-Math.max(1, rounds) * 2)
    .map((m) => ({
      role: m.role,
      content: m.role === 'user' ? (m.text || '') : (m.text || ''),
    }))
}
