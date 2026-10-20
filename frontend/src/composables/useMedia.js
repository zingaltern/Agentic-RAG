/** 视口断点（侧栏抽屉 / 表格卡片化用）。 */
import { onMounted, onUnmounted, ref } from 'vue'

export function useMediaQuery(query) {
  const matches = ref(typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(query).matches
    : false)
  let mql = null
  const onChange = (e) => { matches.value = e.matches }
  onMounted(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    mql = window.matchMedia(query)
    matches.value = mql.matches
    if (mql.addEventListener) mql.addEventListener('change', onChange)
  })
  onUnmounted(() => {
    if (mql && mql.removeEventListener) mql.removeEventListener('change', onChange)
  })
  return matches
}

export function useIsMobile() {
  return useMediaQuery('(max-width: 900px)')
}
