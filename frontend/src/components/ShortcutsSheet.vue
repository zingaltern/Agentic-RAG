<script setup>
/** 键盘快捷键面板（按 ? 打开）：把所有可用快捷操作变成可发现的界面文案。 */
import { shortcutsOpen } from '../composables/useUi'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
const mod = isMac ? '⌘' : 'Ctrl'

const SHORTCUTS = [
  { group: '全局', items: [
    { keys: [mod, 'K'], text: '打开命令面板' },
    { keys: ['?'], text: '显示本快捷键面板' },
    { keys: ['Esc'], text: '关闭弹层 / 取消输入' },
    { keys: [mod, '\\'], text: '折叠 / 展开侧栏（宽屏）' },
  ] },
  { group: '问答', items: [
    { keys: ['Enter'], text: '发送问题（中文输入法组合期不触发）' },
    { keys: ['Shift', 'Enter'], text: '输入换行' },
    { keys: [mod, 'Enter'], text: '发送问题（任意焦点下）' },
    { keys: ['↑'], text: '输入框为空时编辑上一条提问' },
    { keys: [mod, '.'], text: '停止当前生成' },
  ] },
]
</script>

<template>
  <el-dialog
    v-model="shortcutsOpen"
    title="键盘快捷键"
    width="560px"
    align-center
    class="shortcuts-dialog"
  >
    <div class="sc-grid">
      <section v-for="group in SHORTCUTS" :key="group.group" class="sc-group">
        <h4>{{ group.group }}</h4>
        <div v-for="item in group.items" :key="item.text" class="sc-row">
          <span class="sc-desc">{{ item.text }}</span>
          <span class="sc-keys">
            <span v-for="k in item.keys" :key="k" class="kbd">{{ k }}</span>
          </span>
        </div>
      </section>
    </div>
    <p class="sc-note muted">
      提示：命令面板（{{ mod }}K）支持直接搜索页面、动作与知识库，回车即可执行。
    </p>
  </el-dialog>
</template>

<style scoped>
.sc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 28px; }
.sc-group h4 {
  margin: 0 0 6px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-tertiary);
}
.sc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--hairline);
}
.sc-desc { font-size: 12.5px; color: var(--text-secondary); }
.sc-keys { display: inline-flex; gap: 4px; flex: none; }
.sc-note { margin: 16px 0 0; }

@media (max-width: 640px) {
  .sc-grid { grid-template-columns: 1fr; gap: 12px; }
}
</style>
