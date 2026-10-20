<script setup>
/**
 * 登录页（Apple 式双栏）：左侧品牌叙事 + 右侧登录卡。
 * - 团队模式：用户名/密码 → /api/auth/login
 * - 个人模式：服务端返回 40001 时直接进入（保留原行为），并给「直接进入」入口
 * - 用户名框也可回车提交；错误内联展示，不弹全局条
 */
import { nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChatDotRound, Collection, Lock, Search, User } from '@element-plus/icons-vue'
import { api } from '../api'
import { friendlyError } from '../api/index.js'
import { setToken } from '../api/base'
import { useAppStore } from '../stores/app'
import { resetKbs } from '../composables/useKbs'

const router = useRouter()
const store = useAppStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const loginError = ref('')
const userInput = ref(null)

const FEATURES = [
  { icon: Search, title: '反思式检索', desc: '评估 → 改写 → 再检索，直到找到足够证据' },
  { icon: ChatDotRound, title: '逐句溯源', desc: '回答带 [n] 引用与已证率，幻觉无处可藏' },
  { icon: Collection, title: '多库隔离', desc: '按知识库分别检索，共享库权限可控' },
]

onMounted(() => nextTick(() => userInput.value && userInput.value.focus()))

async function submit() {
  if (loading.value) return   // 防重复提交：Enter 与表单 submit 可能同时触发
  loginError.value = ''
  if (!username.value.trim() || !password.value) {
    loginError.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
      silent: true,   // 40001=个人模式（无登录功能）→ 下方分支直接恢复进入，不弹全局错
    })
    if (res.code === 0) {
      // 换账号前先清空上一位用户的会话/库选择/知识库缓存，避免串号
      resetKbs()
      store.resetUserState()
      setToken(res.data.token)
      store.setAuth(res.data.user)
      store.pushInfo(`欢迎，${res.data.user.name || res.data.user.username}`)
      router.push('/')
    } else if (res.code === 40001) {
      enterPersonalMode()
    } else {
      loginError.value = friendlyError(res.code, res.message)
    }
  } finally {
    loading.value = false
  }
}

/** 个人模式（认证未启用）：无需账号，直接恢复本地管理员身份。 */
function enterPersonalMode() {
  resetKbs()
  store.resetUserState()
  store.setAuth({ user_id: 'local', username: 'local', name: '个人模式', role: 'admin' })
  store.pushInfo('当前为个人模式，无需登录，已直接进入')
  router.push('/')
}

async function tryPersonalMode() {
  loginError.value = ''
  const res = await api('/api/auth/me', { silent: true, timeout: 12000 })
  if (res.code === 40001) enterPersonalMode()
  else if (res.code === 0) {
    resetKbs()
    store.resetUserState()
    store.setAuth(res.data.user)
    router.push('/')
  } else {
    loginError.value = '当前为团队模式，请输入用户名与密码'
  }
}
</script>

<template>
  <div class="login-page">
    <!-- 左：品牌叙事 -->
    <section class="login-hero">
      <div class="hero-brand">
        <span class="hero-mark"><el-icon><Search /></el-icon></span>
        <span>
          <b>Agentic RAG</b>
          <small>智能知识库</small>
        </span>
      </div>

      <div class="hero-copy">
        <h1>把知识变成<br />可追溯的答案。</h1>
        <p>检索、反思、再检索 —— 每一次回答都能指回原文的那一行。</p>
      </div>

      <ul class="hero-features">
        <li v-for="f in FEATURES" :key="f.title">
          <span class="feat-icon"><el-icon><component :is="f.icon" /></el-icon></span>
          <span class="feat-text">
            <b>{{ f.title }}</b>
            <small>{{ f.desc }}</small>
          </span>
        </li>
      </ul>
    </section>

    <!-- 右：登录卡 -->
    <section class="login-panel">
      <div class="login-card">
        <h2>登录</h2>
        <p class="login-sub">使用团队账号进入工作区</p>

        <div v-if="loginError" class="login-error" role="alert">
          <el-icon><Lock /></el-icon>
          <span>{{ loginError }}</span>
        </div>

        <form class="login-form" @submit.prevent="submit">
          <label class="field">
            <span class="field-label">用户名</span>
            <el-input
              ref="userInput"
              v-model="username"
              size="large"
              placeholder="用户名"
              autocomplete="username"
              :prefix-icon="User"
            />
          </label>
          <label class="field">
            <span class="field-label">密码</span>
            <el-input
              v-model="password"
              size="large"
              type="password"
              show-password
              placeholder="密码"
              autocomplete="current-password"
              :prefix-icon="Lock"
            />
          </label>

          <el-button type="primary" size="large" class="login-submit" :loading="loading" native-type="submit">
            登录
            <el-icon v-if="!loading" class="submit-arrow"><ArrowRight /></el-icon>
          </el-button>
        </form>

        <div class="login-foot">
          <button type="button" class="link-btn" @click="tryPersonalMode">
            个人模式？直接进入
          </button>
          <span class="muted">v1.0</span>
        </div>
      </div>
      <p class="login-tip muted">本地优先部署 · 数据不出内网 · 支持桌面端</p>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100%;
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  background: var(--bg);
}

/* ---------- 左侧品牌区 ---------- */
.login-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 28px;
  padding: 40px 48px;
  overflow: hidden;
  background:
    radial-gradient(720px 420px at 15% 8%, var(--accent-soft-2), transparent 65%),
    radial-gradient(620px 380px at 92% 92%, var(--accent-soft), transparent 70%),
    linear-gradient(160deg, var(--surface) 0%, var(--bg) 100%);
  border-right: 1px solid var(--hairline);
}
html[data-theme="dark"] .login-hero {
  background:
    radial-gradient(720px 420px at 15% 8%, var(--accent-soft), transparent 62%),
    radial-gradient(620px 380px at 92% 92%, var(--fill-1), transparent 70%),
    linear-gradient(160deg, var(--surface) 0%, var(--bg) 100%);
}
html[data-theme="dark"] .login-hero::after { opacity: 0.06; }
.login-hero::after {
  content: '';
  position: absolute;
  right: -80px;
  top: -60px;
  width: 320px;
  height: 320px;
  border-radius: 50%;
  background: var(--accent-gradient);
  opacity: 0.12;
  filter: blur(8px);
  pointer-events: none;
}
.hero-brand { display: flex; align-items: center; gap: 11px; }
.hero-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 13px;
  font-size: 19px;
  color: #fff;
  background: var(--accent-gradient);
  box-shadow: 0 10px 24px var(--accent-ring), inset 0 1px 0 rgba(255, 255, 255, 0.4);
}
.hero-brand b { display: block; font-size: 15px; letter-spacing: -0.01em; }
.hero-brand small { font-size: 11px; color: var(--text-tertiary); }

.hero-copy { max-width: 460px; }
.hero-copy h1 {
  margin: 0;
  font-size: clamp(30px, 3.4vw, 42px);
  line-height: 1.16;
  letter-spacing: -0.035em;
  font-weight: 700;
}
.hero-copy p {
  margin: 14px 0 0;
  font-size: 14.5px;
  line-height: 1.7;
  color: var(--text-secondary);
  max-width: 400px;
}

.hero-features { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.hero-features li { display: flex; gap: 12px; align-items: flex-start; }
.feat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 10px;
  color: var(--accent);
  background: var(--surface);
  border: 1px solid var(--hairline);
  box-shadow: var(--shadow-1);
}
.feat-text { display: flex; flex-direction: column; }
.feat-text b { font-size: 13px; font-weight: 600; }
.feat-text small { font-size: 12px; color: var(--text-tertiary); line-height: 1.55; }

/* ---------- 右侧登录卡 ---------- */
.login-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 40px 24px;
}
.login-card {
  width: min(392px, 100%);
  padding: 30px 30px 22px;
  border-radius: var(--radius-xl);
  background: var(--surface);
  border: 1px solid var(--hairline);
  box-shadow: var(--shadow-2);
  animation: fade-up 420ms var(--ease) both;
}
.login-card h2 { margin: 0; font-size: 22px; letter-spacing: -0.025em; }
.login-sub { margin: 5px 0 22px; font-size: 13px; color: var(--text-secondary); }

.login-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
  line-height: 1.5;
}

.login-form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; font-weight: 500; color: var(--text-secondary); }
.login-submit { width: 100%; margin-top: 6px; justify-content: center; }
.submit-arrow { margin-left: 6px; }

.login-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}
.link-btn {
  border: none;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
}
.link-btn:hover { text-decoration: underline; }
.login-tip { margin: 0; text-align: center; }

@media (max-width: 900px) {
  .login-page { grid-template-columns: 1fr; }
  .login-hero { display: none; }
  .login-panel { min-height: 100%; padding: 24px 16px; background: radial-gradient(600px 380px at 50% 0%, var(--accent-soft), transparent 70%), var(--bg); }
  .login-card { padding: 24px 20px 18px; }
}
</style>
