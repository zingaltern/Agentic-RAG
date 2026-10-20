/**
 * 来源链接安全化：只放行 http/https。
 * 后端 sources[].url 可能来自联网搜索，属于外部输入；直接绑定 href 会允许
 * `javascript:` / `data:` 等 scheme 执行脚本，因此统一在渲染前过滤。
 */
export function safeHttpUrl(input) {
  if (!input) return ''
  const raw = String(input).trim()
  // 来源必须是绝对 http(s) URL：相对路径/裸字符串对“打开原文”没有意义，
  // 也不该被 new URL(base) 补成 http://localhost/xxx 后当成合法外链。
  if (!/^https?:\/\//i.test(raw)) return ''
  try {
    const url = new URL(raw)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return ''
    return url.href
  } catch {
    return ''
  }
}

/** 来源域名（用于网页来源的展示，避免把 URL 塞进“页码”列）。 */
export function hostOf(input) {
  const safe = safeHttpUrl(input)
  if (!safe) return ''
  try {
    return new URL(safe).host
  } catch {
    return ''
  }
}
