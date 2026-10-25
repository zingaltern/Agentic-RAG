/**
 * 密码策略（与后端 user_store.validate_password_strength 对齐的最小前端版本）。
 * 后端仍会做最终裁决；前端提前拦截只是为了少一次往返、少一条后端错误。
 */
export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_RULE = /^(?=.*[A-Za-z])(?=.*\d).{8,}$/
export const PASSWORD_HINT = '至少 8 位，且同时包含字母和数字'

/** @returns {string} 空串表示通过，否则返回可直接展示的中文原因。 */
export function validatePassword(value) {
  const pw = String(value || '')
  if (pw.length < PASSWORD_MIN_LENGTH) return `密码至少 ${PASSWORD_MIN_LENGTH} 位`
  if (!/[A-Za-z]/.test(pw) || !/\d/.test(pw)) return '密码需同时包含字母和数字'
  return ''
}
