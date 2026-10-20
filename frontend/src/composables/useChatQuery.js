/**
 * 问答请求的「单一在途控制器」。
 * --------------------------------------------------------------------------
 * 为什么放在模块级而不是组件内：
 * - 会话状态（chat/loading）在 Pinia，组件只是视图；切页/清空/登出时组件可能已卸载，
 *   但请求仍在飞。用模块级 controller + generation 才能保证：
 *   1) 任一入口（视图、命令面板、登出、401）都能停掉同一个请求；
 *   2) 被取代的旧请求回来时不会把答案写进新会话（generation 不一致直接丢弃）；
 *   3) 卸载后仍可 abort，避免响应落到已销毁组件。
 * 不放 Pinia state：AbortController 不是可序列化状态，也不该被 devtools/持久化碰。
 */

let controller = null
let generation = 0

/** 开始一次新请求：若已有在途请求先中断（语义上等同"重新生成"）。 */
export function beginQuery() {
  if (controller) controller.abort()
  controller = new AbortController()
  generation += 1
  return { signal: controller.signal, generation }
}

/** 主动中止当前请求；返回是否真的中断了一个在途请求。 */
export function abortQuery() {
  if (!controller) return false
  controller.abort()
  controller = null
  generation += 1
  return true
}

/** 请求结束后调用：只清理自己那一次，不误伤后发请求。 */
export function finishQuery(token) {
  if (token && token.generation === generation && controller && controller.signal === token.signal) {
    controller = null
  }
}

/** 该 generation 是否仍是最新（旧请求回来的第一道闸门）。 */
export function isCurrent(generationId) {
  return generationId === generation
}

export function hasInflight() {
  return Boolean(controller) && !controller.signal.aborted
}
