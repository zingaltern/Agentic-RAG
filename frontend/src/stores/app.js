import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    errors: [],        // 全局提示条（{id, text, type}）
    authenticated: false,  // V5.1：登录态（auth_enabled=false 个人模式视为已登录）
    currentUser: null,     // {user_id, username, name, role}
    webSearch: false,      // A5：联网搜索开关（存 store：切页不丢）
    sessionId: localStorage.getItem('rag_session_id') || '',   // 13.13 T3：服务端会话 ID（dialogue 启用时后端接管历史）
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
    // V5.1 登录态
    setAuth(user) {
      this.authenticated = true
      this.currentUser = user
    },
    clearAuth() {
      this.authenticated = false
      this.currentUser = null
    },
    // 13.13 T3：会话 ID 惰性生成并持久化（刷新页面/重开应用延续同一服务端会话）
    ensureSessionId() {
      if (!this.sessionId) {
        this.sessionId = (crypto.randomUUID && crypto.randomUUID())
          || `s-${Date.now()}-${Math.random().toString(36).slice(2)}`
        localStorage.setItem('rag_session_id', this.sessionId)
      }
      return this.sessionId
    },
    // 新会话：清空对话/登出时调用（服务端历史与澄清计数随之重置）
    resetSession() {
      this.sessionId = ''
      localStorage.removeItem('rag_session_id')
    },
    // 主动登出：清登录态 + 会话数据（防止同浏览器下一位登录用户看到上一位的对话/审计号）
    logout() {
      this.clearAuth()
      this.chat = { messages: [], loading: false, query: '' }
      this.resetSession()
    },
  },
})
