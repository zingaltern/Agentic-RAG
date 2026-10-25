<script setup>
/**
 * 知识库管理（卡片栅格版）
 * - 每个库显示文档数（/api/documents?size=1 取 total）、归属与创建时间
 * - 一键「进入」：切到该库并跳转问答；「查看文档」跳文档页
 * - 新建向导：名称/描述/归属（仅管理员可选共享/私有）
 * - 默认库禁止删除（服务端规则，前端提前置灰并解释原因）
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, Delete, Document, FolderAdd, FolderOpened, Lock, User } from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import StatTile from '../components/StatTile.vue'
import { api, friendlyError } from '../api'
import { useAppStore } from '../stores/app'
import { useKbs } from '../composables/useKbs'
import { formatRelative } from '../utils/format'
import { confirmDialog } from '../utils/confirm'

const router = useRouter()
const store = useAppStore()
const { kbs, load, loading, error: kbsError, docCount } = useKbs()

const counts = ref({})          // kb_id → 文档数
const countsLoading = ref(true)
const dialog = ref(false)
const saving = ref(false)
const form = ref({ name: '', description: '', scope: 'shared' })

const isAdmin = () => store.currentUser && store.currentUser.role === 'admin'
const isPersonalMode = () => store.currentUser && store.currentUser.user_id === 'local'

const totalDocs = computed(() => Object.values(counts.value).reduce((sum, n) => sum + (n || 0), 0))
const sharedCount = computed(() => kbs.value.filter((k) => k.scope === 'shared').length)

onMounted(async () => {
  await load(true)
  refreshCounts()
})

async function refreshCounts() {
  countsLoading.value = true
  try {
    const entries = await Promise.all(kbs.value.map(async (kb) => [kb.kb_id, await docCount(kb.kb_id)]))
    counts.value = Object.fromEntries(entries)
  } finally {
    // 单个库请求异常也不能让计数永久停在“…”
    countsLoading.value = false
  }
}

/** 列表 + 计数一起刷新：必须先 await 列表，否则计数基于旧列表。 */
async function refreshAll() {
  await load(true)
  if (!kbsError.value) await refreshCounts()
}

/** 加载失败后的重试。 */
async function retryLoad() {
  await refreshAll()
}

function readOnlyKb(row) {
  return row.scope === 'shared' && !isAdmin()
}

function openKb(kb) {
  store.setKbId(kb.kb_id)
  store.pushInfo(`已切换到「${kb.name}」`)
  router.push('/')
}

function openDocs(kb) {
  store.setKbId(kb.kb_id)
  router.push('/documents')
}

async function createKb() {
  if (!form.value.name.trim()) { store.pushError('请输入知识库名称'); return }
  saving.value = true
  try {
    const payload = { ...form.value, name: form.value.name.trim() }
    // 普通用户无权建共享库（服务端强制 private，这里前端对齐语义）
    if (!(isAdmin() && !isPersonalMode())) payload.scope = 'private'
    const res = await api('/api/kbs', { method: 'POST', body: JSON.stringify(payload) })
    if (res.code === 0) {
      store.pushInfo(`知识库「${res.data.name}」已创建`)
      dialog.value = false
      form.value = { name: '', description: '', scope: 'shared' }
      await load(true)
      refreshCounts()
    }
  } finally {
    saving.value = false
  }
}

async function removeKb(row) {
  const ok = await confirmDialog(
    `确认删除「${row.name}」？该库中的文档与向量会一并清理，不可恢复。`,
    '删除知识库',
    { confirmButtonText: '删除' },
  )
  if (!ok) return
  const res = await api(`/api/kbs/${row.kb_id}`, { method: 'DELETE', silent: true })
  if (res.code === 0) {
    store.pushInfo(`已删除「${row.name}」`)
    if (store.kbId === row.kb_id) store.setKbId('default')
    await load(true)
    refreshCounts()
  } else {
    store.pushError(`删除失败：${friendlyError(res.code, res.message)}`)
  }
}
</script>

<template>
  <div class="page-scroll">
    <div class="page">
      <PageHeader title="知识库管理" :subtitle="`每个知识库相互隔离 · 当前：${(kbs.find((k) => k.kb_id === store.kbId) || {}).name || store.kbId}`">
        <template #actions>
          <el-button @click="refreshAll">
            <el-icon style="margin-right: 6px"><FolderOpened /></el-icon>刷新
          </el-button>
          <el-button type="primary" @click="dialog = true">
            <el-icon style="margin-right: 6px"><FolderAdd /></el-icon>新建知识库
          </el-button>
        </template>
      </PageHeader>

      <div class="stat-row">
        <StatTile label="知识库" :value="kbs.length" hint="可访问的库总数" :icon="Collection" tone="accent" />
        <StatTile label="文档" :value="countsLoading ? '…' : totalDocs" hint="全库文档合计" :icon="Document" />
        <StatTile label="共享库" :value="sharedCount" hint="管理员统一维护" :icon="User" />
        <StatTile label="私有库" :value="kbs.length - sharedCount" hint="仅创建者可见" :icon="Lock" />
      </div>

      <div v-if="loading && !kbs.length" class="kb-grid">
        <div v-for="i in 3" :key="i" class="kb-card kb-card--skeleton" />
      </div>

      <div v-else-if="kbs.length" class="kb-grid">
        <article
          v-for="kb in kbs"
          :key="kb.kb_id"
          class="kb-card"
          :class="{ 'is-current': kb.kb_id === store.kbId }"
        >
          <div class="kb-head">
            <span class="kb-icon"><el-icon><FolderOpened /></el-icon></span>
            <div class="kb-title">
              <h3 class="truncate" :title="kb.name">{{ kb.name }}</h3>
              <span class="kb-id mono">{{ kb.kb_id }}</span>
            </div>
            <el-tag v-if="kb.kb_id === store.kbId" size="small" type="success" effect="plain">当前</el-tag>
          </div>

          <p class="kb-desc">{{ kb.description || '暂无描述' }}</p>

          <div class="kb-meta">
            <span class="kb-count">{{ countsLoading ? '…' : (counts[kb.kb_id] ?? 0) }}</span>
            <span class="muted">份文档</span>
            <span class="kb-sep" />
            <el-tag v-if="kb.scope === 'shared'" size="small" type="info">共享</el-tag>
            <el-tag v-else size="small" type="warning">私有 · {{ kb.owner_name || kb.owner }}</el-tag>
            <span class="spacer" />
            <span class="muted">{{ formatRelative(kb.created_at * 1000) }}创建</span>
          </div>

          <div class="kb-actions">
            <el-button size="small" type="primary" plain @click="openKb(kb)">进入问答</el-button>
            <el-button size="small" @click="openDocs(kb)">文档</el-button>
            <span class="spacer" />
            <el-tooltip v-if="isAdmin() && kb.kb_id === 'default'" content="默认知识库不可删除" placement="top">
              <span><el-button size="small" disabled>删除</el-button></span>
            </el-tooltip>
            <el-tooltip v-else-if="readOnlyKb(kb)" content="共享库仅管理员可删除" placement="top">
              <span><el-button size="small" disabled><el-icon><Lock /></el-icon></el-button></span>
            </el-tooltip>
            <el-button v-else size="small" text @click="removeKb(kb)">
              <el-icon style="margin-right: 4px"><Delete /></el-icon>删除
            </el-button>
          </div>
        </article>
      </div>

      <div v-else-if="kbsError && !kbs.length" class="kb-error">
        <el-icon class="kb-error-icon"><FolderOpened /></el-icon>
        <p class="kb-error-title">知识库列表加载失败</p>
        <p class="muted">{{ kbsError }}</p>
        <el-button type="primary" plain @click="retryLoad">重新加载</el-button>
      </div>

      <EmptyState
        v-else-if="!kbs.length"
        :icon="FolderOpened"
        title="还没有知识库"
        description="新建一个知识库，把相关文档放进去，问答就会只在这个范围内检索。"
      >
        <el-button type="primary" @click="dialog = true">新建知识库</el-button>
      </EmptyState>

      <p class="muted foot-note">
        提示：上传与问答都作用于「当前知识库」；切换库不会清空对话，但后续提问会在新库内检索。
      </p>
    </div>

    <el-dialog v-model="dialog" title="新建知识库" width="440px" align-center>
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：HR Policies / 报销制度" maxlength="40" show-word-limit />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="这个库放什么内容、给谁用" />
        </el-form-item>
        <el-form-item v-if="isAdmin() && !isPersonalMode()" label="归属">
          <el-select v-model="form.scope" style="width: 100%">
            <el-option label="共享（所有成员可见，管理员维护）" value="shared" />
            <el-option label="私有（仅自己可见）" value="private" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createKb">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.stat-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }

.kb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.kb-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--hairline);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease), transform var(--dur) var(--ease);
}
.kb-card:hover { border-color: var(--hairline-strong); box-shadow: var(--shadow-2); transform: translateY(-1px); }
.kb-card.is-current { border-color: var(--accent-soft-2); box-shadow: 0 0 0 3px var(--accent-soft); }
.kb-card--skeleton {
  min-height: 168px;
  border-style: dashed;
  background: linear-gradient(100deg, var(--fill-1) 30%, var(--fill-2) 50%, var(--fill-1) 70%);
  background-size: 200% 100%;
  animation: shimmer-bg 1.4s linear infinite;
}
@keyframes shimmer-bg { from { background-position: 120% 0; } to { background-position: -120% 0; } }

.kb-head { display: flex; align-items: center; gap: 10px; }
.kb-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 10px;
  font-size: 16px;
  color: var(--accent);
  background: var(--accent-soft);
}
.kb-title { min-width: 0; flex: 1; }
.kb-title h3 { margin: 0; font-size: 14px; font-weight: 650; }
.kb-id { font-size: 10.5px; color: var(--text-quaternary); }
.kb-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-tertiary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 38px;
}
.kb-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.kb-count { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.kb-sep { width: 1px; height: 12px; background: var(--hairline-strong); margin: 0 2px; }
.kb-actions { display: flex; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid var(--hairline); }
.spacer { flex: 1; }
.foot-note { text-align: center; }
.kb-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 36px 20px;
  text-align: center;
  border: 1px dashed var(--hairline-strong);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.kb-error-icon { font-size: 26px; color: var(--danger); }
.kb-error-title { margin: 2px 0 0; font-size: 14px; font-weight: 600; }

@media (max-width: 1100px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .kb-grid { grid-template-columns: 1fr; }
}
</style>
