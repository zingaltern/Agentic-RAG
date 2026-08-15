import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    errors: [],        // 全局提示条（{id, text, type}）
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
    // V5.1 登录态
    setAuth(user) {
      this.authenticated = true
      this.currentUser = user
    },
    clearAuth() {
      this.authenticated = false
      this.currentUser = null
    },
    // 主动登出：清登录态 + 会话数据（防止同浏览器下一位登录用户看到上一位的对话/审计号）
    logout() {
      this.clearAuth()
      this.chat = { messages: [], loading: false, query: '' }
    },
  },
})
