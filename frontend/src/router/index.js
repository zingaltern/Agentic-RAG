import { createRouter, createWebHistory } from 'vue-router'
import QueryConsole from '../views/QueryConsole.vue'
import DocumentsView from '../views/DocumentsView.vue'
import KbsView from '../views/KbsView.vue'
import SettingsView from '../views/SettingsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'query', component: QueryConsole },
    { path: '/documents', name: 'documents', component: DocumentsView },
    { path: '/kbs', name: 'kbs', component: KbsView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})
