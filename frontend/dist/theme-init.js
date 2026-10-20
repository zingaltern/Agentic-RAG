/**
 * 首帧主题引导（parser-blocking，放在 <head> 中、module 之前）。
 * --------------------------------------------------------------------------
 * 为什么是独立文件而不是内联脚本：
 * FastAPI 的 CSP 是 `script-src 'self'`（无 unsafe-inline/nonce/hash），
 * 内联脚本会被浏览器直接拦截，导致深色系统下先白后黑的闪烁。
 * 本文件由 vite 从 public/ 原样复制到 dist 根目录，由 FastAPI 静态回退服务，
 * 默认就是 'self'，不需要放宽 CSP。
 *
 * 只做「首帧」需要的三件事：data-theme / data-accent / color-scheme。
 * 之后 useTheme.js 接管（包括跟随系统变化）。
 */
(function () {
  try {
    var mode = localStorage.getItem('rag_theme_mode') || 'system'
    var accent = localStorage.getItem('rag_accent') || 'coral'
    var mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
    var dark = mode === 'dark' || (mode === 'system' && mql && mql.matches)
    var el = document.documentElement

    el.dataset.theme = dark ? 'dark' : 'light'
    el.dataset.accent = accent
    if (el.classList) el.classList.toggle('dark', !!dark)
    el.style.colorScheme = dark ? 'dark' : 'light'

    // 与 useTheme.applyTheme 保持一致，避免地址栏颜色与页面主题脱节
    var meta = document.querySelector('meta[name="theme-color"]')
    if (meta) meta.setAttribute('content', dark ? '#161618' : '#f5f5f7')
  } catch (e) {
    /* localStorage/隐私模式不可用时保持默认浅色，不阻断渲染 */
  }
})()
