/**
 * 外部后端不可达恢复对话框的纯逻辑（不依赖 electron，可用 node:test 直接单测）。
 * 按钮顺序与 resolveRecoveryAction() 的映射必须保持一致：
 *   0 = 使用本机服务（清空 external_base 后继续本机启动）
 *   1 = 重试（重新探测外部后端）
 *   2 / 关闭 = 退出
 */
'use strict'

const ACTIONS = Object.freeze({
  USE_LOCAL: 'use-local',
  RETRY: 'retry',
  QUIT: 'quit',
})

const BUTTONS = Object.freeze(['使用本机服务', '重试', '退出'])
const DEFAULT_ID = 0
const CANCEL_ID = 2

/** 构造 Electron dialog.showMessageBox 的参数；文案必须给出可操作的恢复路径。 */
function buildExternalUnavailableDialog(external, timeoutMs = 60000) {
  const seconds = Math.max(1, Math.round(Number(timeoutMs) / 1000) || 60)
  return {
    type: 'error',
    title: '外部后端不可达',
    message: '无法连接外部后端',
    detail: `${seconds}s 内 /api/health 未返回 ok（${external}）。\n\n可选择“使用本机服务”（将清空外部后端配置），或检查目标后端后重试。`,
    buttons: [...BUTTONS],
    defaultId: DEFAULT_ID,
    cancelId: CANCEL_ID,
    noLink: true,
  }
}

/** 把原生对话框的 response 索引映射为动作；未知/取消一律退出。 */
function resolveRecoveryAction(response) {
  if (Number(response) === 0) return ACTIONS.USE_LOCAL
  if (Number(response) === 1) return ACTIONS.RETRY
  return ACTIONS.QUIT
}

module.exports = {
  ACTIONS,
  BUTTONS,
  buildExternalUnavailableDialog,
  resolveRecoveryAction,
}
