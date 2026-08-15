import { createRouter, createWebHistory } from 'vue-router'
import QueryConsole from '../views/QueryConsole.vue'
import DocumentsView from '../views/DocumentsView.vue'
import KbsView from '../views/KbsView.vue'
import SettingsView from '../views/SettingsView.vue'

// 登录视图由 App.vue 依据 authenticated 状态渲染（v-if），无需独立 /login 路由；
// 保留 catch-all 兜底避免未知路径空白。
export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'query', component: QueryConsole },
    { path: '/documents', name: 'documents', component: DocumentsView },
    { path: '/kbs', name: 'kbs', component: KbsView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
