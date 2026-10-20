import { createRouter, createWebHistory } from 'vue-router'

// 路由级懒加载：首屏只加载问答页，其余视图按需拉取（首包体积下降约 60%）
const QueryConsole = () => import('../views/QueryConsole.vue')
const DocumentsView = () => import('../views/DocumentsView.vue')
const KbsView = () => import('../views/KbsView.vue')
const SettingsView = () => import('../views/SettingsView.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'query', component: QueryConsole, meta: { title: '问答控制台' } },
    { path: '/documents', name: 'documents', component: DocumentsView, meta: { title: '文档管理' } },
    { path: '/kbs', name: 'kbs', component: KbsView, meta: { title: '知识库管理' } },
    { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '设置总览' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

// 部署后 chunk 哈希会变化：仍停留在旧页面的标签页点击懒加载路由时，
// 动态 import 会 404（SPA 回退返回 index.html，MIME 不对），表现为“点击没反应”。
// 这里捕获一次并整页跳转到目标路由（10 秒内只做一次，避免死循环）。
router.onError((error, to) => {
  const message = String((error && error.message) || error || '')
  if (!/Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(message)) return
  let last = 0
  try { last = Number(sessionStorage.getItem('rag_chunk_reload') || 0) } catch { last = 0 }
  if (Date.now() - last < 10000) return
  try { sessionStorage.setItem('rag_chunk_reload', String(Date.now())) } catch { /* ignore */ }
  const target = to && to.fullPath ? to.fullPath : window.location.pathname + window.location.search
  window.location.assign(target)
})

// 每个页面写自己的 document.title（多标签页/桌面窗口可区分）
router.afterEach((to) => {
  const title = to.meta && to.meta.title
  document.title = title ? `${title} · Agentic RAG` : 'Agentic RAG 智能知识库'
})

export default router
