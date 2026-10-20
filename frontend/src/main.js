import { createApp } from 'vue'
import { createPinia } from 'pinia'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
// 组件级样式（按需）；dark 变量包只含 CSS 变量，体积极小
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import { installElement } from './plugins/element'
// 顺序有意义：element-plus 组件样式 → 本设计系统（同特异性时后者生威）
import './style.css'
import { initTheme } from './composables/useTheme'

// 先落地主题（避免深色系统下首帧白闪）
initTheme()

const app = createApp(App)
installElement(app)      // 按需注册的 Element Plus 组件 + v-loading
app.use(createPinia())
app.use(router)

// 语言包：App.vue 以 <el-config-provider :locale="zhCn"> 注入
app.config.globalProperties.$locale = zhCn
app.mount('#app')
