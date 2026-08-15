<script setup>
import { storeToRefs } from 'pinia'
import { CircleCheckFilled, CircleCloseFilled, Close } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'

/** 全局提示条：info（成功/操作反馈，绿色）与 error（红色）两类。 */
const store = useAppStore()
const { errors } = storeToRefs(store)
</script>

<template>
  <div class="msg-bar">
    <transition-group name="msg">
      <div
        v-for="e in errors"
        :key="e.id"
        :class="['msg-item', e.type === 'info' ? 'is-info' : 'is-error']"
      >
        <button class="msg-close" title="关闭" @click="store.dismissError(e.id)">
          <el-icon><Close /></el-icon>
        </button>
        <span class="msg-text">{{ e.text }}</span>
        <el-icon class="msg-status">
          <CircleCheckFilled v-if="e.type === 'info'" />
          <CircleCloseFilled v-else />
        </el-icon>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.msg-bar {
  position: fixed;
  top: 18px;
  right: 18px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 440px;
}
.msg-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
  box-shadow: 0 6px 20px rgba(15, 23, 42, .14);
}
.is-info {
  background: linear-gradient(180deg, #eefbf3, #e3f7ea);
  border: 1px solid #b7ebc9;
  color: #1d7a45;
}
.is-error {
  background: linear-gradient(180deg, #fdf0ed, #fbe8e4);
  border: 1px solid #f2c4bb;
  color: #c74f3f;
}
.msg-text { flex: 1; word-break: break-all; }
.msg-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  flex: none;
  transition: background .15s;
}
.is-info .msg-close { color: #3fa66f; }
.is-error .msg-close { color: #d4655a; }
.msg-close:hover { background: rgba(0, 0, 0, .06); }
.msg-status { font-size: 16px; flex: none; }

.msg-enter-active, .msg-leave-active { transition: all .25s ease; }
.msg-enter-from { opacity: 0; transform: translateX(24px); }
.msg-leave-to { opacity: 0; transform: translateX(24px); }
</style>