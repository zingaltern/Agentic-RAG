import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    errors: [],        // 全局错误提示条（{id, text}）
    lastTraceId: '',   // 最近一次请求的 trace_id（S6 面板展示）
  }),
  actions: {
    pushError(text) {
      const id = Date.now() + Math.random()
      this.errors.push({ id, text })
      setTimeout(() => {
        this.errors = this.errors.filter((e) => e.id !== id)
      }, 6000)
    },
    setTraceId(traceId) {
      if (traceId) this.lastTraceId = traceId
    },
  },
})
