import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    errors: [],        // 全局提示条（{id, text, type}）
    lastTraceId: '',   // 最近一次请求的 trace_id（S6 面板展示）
    authenticated: false,  // V5.1：登录态（auth_enabled=false 个人模式视为已登录）
    currentUser: null,     // {user_id, username, name, role}
    chat: {            // 问答控制台会话（存 store：切页不丢）
      messages: [],    // {role:'user'|'assistant', text, result, time}
      loading: false,
      query: '',
    },
  }),
  actions: {
    pushInfo(text) {
      this._push(text, 'info')
    },
    pushError(text) {
      this._push(text, 'error')
    },
    _push(text, type) {
      const id = Date.now() + Math.random()
      this.errors.push({ id, text, type })
      setTimeout(() => {
        this.errors = this.errors.filter((e) => e.id !== id)
      }, 6000)
    },
    dismissError(id) {
      this.errors = this.errors.filter((e) => e.id !== id)
    },
    setTraceId(traceId) {
      if (traceId) this.lastTraceId = traceId
    },
    // V5.1 登录态
    setAuth(user) {
      this.authenticated = true
      this.currentUser = user
    },
    clearAuth() {
      this.authenticated = false
      this.currentUser = null
    },
  },
})
