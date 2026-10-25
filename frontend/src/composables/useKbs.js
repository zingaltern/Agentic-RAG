/**
 * 知识库列表共享缓存：侧栏 / 问答 / 文档 / 知识库页 / 命令面板共用一次请求。
 * - 并发调用只发一次请求（inflight 复用）
 * - force=true 会等旧请求结束后再发新请求（不能直接复用旧 inflight，否则刷新无效）
 * - resetKbs()：登出/换号时清空，并用 epoch 丢弃仍在飞的旧响应（防止 A 的私有库串到 B）
 */
import { ref } from 'vue'
import { api } from '../api/index.js'

const kbs = ref([])
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
let inflight = null      // { promise }
let epoch = 0            // 缓存世代：reset 后旧的响应一律丢弃

async function fetchKbs(myEpoch) {
  loading.value = true
  error.value = ''
  try {
    const res = await api('/api/kbs')
    if (myEpoch !== epoch) return kbs.value   // 已被 reset（登出/换号）
    if (res.code === 0) {
      kbs.value = res.data.kbs || []
      loaded.value = true
    } else {
      // 失败要允许重试：loaded 置 false，下次页面进入会重新请求
      error.value = res.message || '知识库列表加载失败'
      loaded.value = false
    }
    return kbs.value
  } finally {
    if (myEpoch === epoch) loading.value = false
  }
}

/** 清空缓存（登出/401/切换账号时由上层调用）。 */
export function resetKbs() {
  epoch += 1
  inflight = null
  kbs.value = []
  loaded.value = false
  error.value = ''
  loading.value = false
}

export function useKbs() {
  async function load(force = false) {
    if (inflight) {
      if (!force) return inflight.promise
      await inflight.promise.catch(() => {})
    }
    if (!force && loaded.value) return kbs.value

    const myEpoch = epoch
    const promise = fetchKbs(myEpoch).finally(() => {
      if (inflight && inflight.promise === promise) inflight = null
    })
    inflight = { promise }
    return promise
  }

  /** 单库文档数（size=1 只取 total，成本极低）；失败返回 null 供 UI 显示 — */
  async function docCount(kbId) {
    if (!kbId) return 0
    const res = await api(`/api/documents?page=1&size=1&kb_id=${encodeURIComponent(kbId)}`, { silent: true })
    return res.code === 0 ? (res.data.total || 0) : null
  }

  return { kbs, loading, loaded, error, load, docCount }
}
