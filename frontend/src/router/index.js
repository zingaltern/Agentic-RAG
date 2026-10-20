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

// 每个页面写自己的 document.title（多标签页/桌面窗口可区分）
router.afterEach((to) => {
  const title = to.meta && to.meta.title
  document.title = title ? `${title} · Agentic RAG` : 'Agentic RAG 智能知识库'
})

export default router
