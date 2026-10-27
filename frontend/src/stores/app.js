import { defineStore } from 'pinia'
import { clearToken } from '../api/base.js'
import { abortQuery } from '../composables/useChatQuery.js'

/** localStorage 安全读写（Safari 隐私模式 / 单测环境下降级为空）。 */
function readStored(key, fallback = '') {
  try {
    return localStorage.getItem(key) || fallback
  } catch {
    return fallback
  }
}
function writeStored(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch { /* ignore */ }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    errors: [],        // 全局提示条（{id, text, type}）
    authenticated: false,  // V5.1：登录态（auth_enabled=false 个人模式视为已登录）
    currentUser: null,     // {user_id, username, name, role}
    webSearch: false,      // A5：联网搜索开关（存 store：切页不丢）
    // 当前知识库：三处（问答/文档/命令面板）共用同一份选择，切页不丢
    kbId: readStored('rag_kb_id', 'default'),
    sessionId: readStored('rag_session_id'),   // 13.13 T3：服务端会话 ID（dialogue 启用时后端接管历史）
    chat: {            // 问答控制台会话（存 store：切页不丢）
      messages: [],    // {role:'user'|'assistant', text, result, time, id, feedback}
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
      const msg = String(text || '')
      // 同文案在 1.2 秒内只留一条：连续失败（如后端不可达时多次请求）不刷屏
      const dup = this.errors.find((e) => e.text === msg && e.type === type && Date.now() - e.at < 1200)
      if (dup) return
      const id = Date.now() + Math.random()
      this.errors.push({ id, text: msg, type, at: Date.now() })
      if (this.errors.length > 4) this.errors = this.errors.slice(-4)
      setTimeout(() => {
        this.errors = this.errors.filter((e) => e.id !== id)
      }, type === 'error' ? 8000 : 5000)
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
    // 知识库选择（持久化：刷新/重开应用后停在上次使用的库）
    setKbId(kbId) {
      if (!kbId) return
      this.kbId = kbId
      writeStored('rag_kb_id', kbId)
    },
    // 13.13 T3：会话 ID 惰性生成并持久化（刷新页面/重开应用延续同一服务端会话）
    ensureSessionId() {
      if (!this.sessionId) {
        this.sessionId = (crypto.randomUUID && crypto.randomUUID())
          || `s-${Date.now()}-${Math.random().toString(36).slice(2)}`
        writeStored('rag_session_id', this.sessionId)
      }
      return this.sessionId
    },
    // 新会话：清空对话/登出时调用（服务端历史与澄清计数随之重置）
    resetSession() {
      this.sessionId = ''
      writeStored('rag_session_id', '')
    },
    /**
     * 重置「用户态」：会话、在途请求、库选择、提示条、联网开关。
     * 401 失效、主动登出、切换账号都必须走这里，否则上一位用户的
     * chat/session/kb/私有库缓存会泄漏给下一位用户（服务端按 session_id 取历史）。
     * 知识库列表缓存由上层（App.vue）调用 resetKbs() 处理，避免 store 反向依赖 composable。
     */
    resetUserState() {
      abortQuery()
      this.chat = { messages: [], loading: false, query: '' }
      this.errors = []
      this.webSearch = false
      this.setKbId('default')
      this.resetSession()
    },
    // 主动登出：清 Token + 登录态 + 全部会话数据
    logout() {
      this.clearAuth()
      this.resetUserState()
      clearToken()
    },
  },
})
