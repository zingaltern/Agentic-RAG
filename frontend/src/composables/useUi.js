/**
 * 全局 UI 单例状态：命令面板 / 快捷键面板 / 移动端抽屉 / 搜索焦点请求。
 * 放在模块级 ref 而非 Pinia：这些纯视图状态不需要持久化，也不该进业务 store。
 */
import { ref } from 'vue'

export const commandOpen = ref(false)
export const shortcutsOpen = ref(false)
export const mobileNavOpen = ref(false)
/** 问答页监听该计数变化后聚焦输入框（命令面板「新对话」等动作触发）。 */
export const focusComposerTick = ref(0)

export function openCommand() {
  commandOpen.value = true
}
export function closeCommand() {
  commandOpen.value = false
}
export function toggleCommand() {
  commandOpen.value = !commandOpen.value
}
export function openShortcuts() {
  shortcutsOpen.value = true
}
export function requestComposerFocus() {
  focusComposerTick.value += 1
}

/** 身份切换（登录/登出/401）时收起所有全局弹层，避免下一位用户登录后自动看到上一位的面板。 */
export function resetUiState() {
  commandOpen.value = false
  shortcutsOpen.value = false
  mobileNavOpen.value = false
}
