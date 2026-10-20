<script setup>
/**
 * 全局提示（Apple 式 Banner）：顶部居中、毛玻璃、图标 + 文案 + 关闭。
 * info = 操作成功/中性反馈（绿色勾）；error = 失败（红色叹号）。
 * 读屏：role=status + aria-live=polite，避免打断当前朗读。
 */
import { storeToRefs } from 'pinia'
import { CircleCheckFilled, Close, WarningFilled } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const { errors } = storeToRefs(store)
</script>

<template>
  <div class="toast-stack" role="status" aria-live="polite">
    <transition-group name="toast">
      <div
        v-for="e in errors"
        :key="e.id"
        class="toast"
        :class="e.type === 'info' ? 'is-info' : 'is-error'"
      >
        <el-icon class="toast-icon">
          <CircleCheckFilled v-if="e.type === 'info'" />
          <WarningFilled v-else />
        </el-icon>
        <span class="toast-text">{{ e.text }}</span>
        <button type="button" class="toast-close" aria-label="关闭提示" @click="store.dismissError(e.id)">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 4000;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: min(92vw, 520px);
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: 9px;
  max-width: 100%;
  padding: 9px 10px 9px 14px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--hairline);
  background: var(--surface-translucent);
  backdrop-filter: saturate(180%) blur(24px);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  box-shadow: var(--shadow-2);
  font-size: 13px;
  line-height: 1.45;
  color: var(--text);
  pointer-events: auto;
}
.toast-icon { font-size: 16px; flex: none; }
.is-info .toast-icon { color: var(--success); }
.is-error .toast-icon { color: var(--danger); }
.toast-text { flex: 1; min-width: 0; word-break: break-word; }
.toast-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex: none;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.toast-close:hover { background: var(--fill-2); color: var(--text); }

.toast-enter-active, .toast-leave-active { transition: all 260ms var(--ease); }
.toast-enter-from { opacity: 0; transform: translateY(-10px) scale(0.97); }
.toast-leave-to { opacity: 0; transform: translateY(-6px); }
.toast-move { transition: transform 260ms var(--ease); }
</style>
