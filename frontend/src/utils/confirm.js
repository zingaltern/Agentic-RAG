/**
 * 确认/输入弹窗统一封装。
 * Element Plus 的命令式 MessageBox 不读 <el-config-provider> 的 locale，
 * 默认按钮是英文（OK/Cancel）；这里集中给中文默认值，避免每个调用点各写一份、漏写就变英文。
 */
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { PASSWORD_HINT, PASSWORD_RULE } from './password.js'

const BUTTONS = { confirmButtonText: '确认', cancelButtonText: '取消' }

/**
 * @returns {Promise<boolean>} 用户确认返回 true；取消/关闭返回 false（不抛异常）。
 */
export async function confirmDialog(message, title = '请确认', options = {}) {
  const result = await ElMessageBox.confirm(message, title, {
    type: 'warning',
    ...BUTTONS,
    ...options,
  }).catch(() => false)
  return result !== false
}

/**
 * 密码类输入（默认掩码）。
 * @returns {Promise<string|null>} 取消返回 null。
 */
export async function promptPassword(message, title = '请输入', options = {}) {
  const result = await ElMessageBox.prompt(message, title, {
    inputType: 'password',
    // 默认即与后端策略一致：≥8 位且含字母+数字（调用方可覆盖）
    inputPattern: PASSWORD_RULE,
    inputErrorMessage: PASSWORD_HINT,
    ...BUTTONS,
    ...options,
  }).catch(() => null)
  return result && typeof result.value === 'string' ? result.value : null
}
