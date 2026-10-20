/** 展示层格式化工具（纯函数，便于 node --test 覆盖）。 */

export function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

export function formatBytes(bytes) {
  const n = toNumber(bytes, 0)
  if (n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1)
  const v = n / 1024 ** i
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function formatDuration(ms) {
  const n = toNumber(ms, 0)
  if (n < 1000) return `${Math.round(n)} ms`
  if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} s`
  const m = Math.floor(n / 60000)
  const s = Math.round((n % 60000) / 1000)
  return `${m} 分 ${s} 秒`
}

export function formatPercent(value, digits = 0) {
  const n = toNumber(value, 0)
  return `${(n * 100).toFixed(digits)}%`
}

function pad(n) {
  return String(n).padStart(2, '0')
}

export function formatDateTime(input) {
  // 0 是“无时间”的常见哨兵值（如新建未更新的文档），不要渲染成 1970 年
  if (input === 0 || input === '0' || !input) return '—'
  const d = input instanceof Date ? input : new Date(typeof input === 'number' && input < 1e12 ? input * 1000 : input)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatClock(input) {
  if (!input) return ''
  const d = input instanceof Date ? input : new Date(input)
  if (Number.isNaN(d.getTime())) return ''
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 相对时间：刚刚 / n 分钟前 / n 小时前 / 昨天 / 具体日期。 */
export function formatRelative(input, now = Date.now()) {
  if (input === 0 || input === '0' || !input) return '—'
  const d = input instanceof Date ? input : new Date(typeof input === 'number' && input < 1e12 ? input * 1000 : input)
  if (Number.isNaN(d.getTime())) return '—'
  const diff = now - d.getTime()
  if (diff < 45 * 1000) return '刚刚'
  if (diff < 3600 * 1000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 24 * 3600 * 1000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 48 * 3600 * 1000) return '昨天'
  if (diff < 7 * 24 * 3600 * 1000) return `${Math.floor(diff / 86400000)} 天前`
  return formatDateTime(d).slice(0, 10)
}

/** 取名字首字符做头像（中文取末字更自然，英文取首字母）。 */
export function initials(name) {
  const s = String(name || '').trim()
  if (!s) return '?'
  if (/[\u4e00-\u9fa5]/.test(s)) return s.slice(-1)
  return s.slice(0, 1).toUpperCase()
}

/** 从文件名猜文档类型（用于图标/筛选）。 */
export function fileKind(filename) {
  const ext = String(filename || '').split('.').pop().toLowerCase()
  if (['pdf'].includes(ext)) return 'pdf'
  if (['doc', 'docx'].includes(ext)) return 'doc'
  if (['xls', 'xlsx', 'csv', 'tsv'].includes(ext)) return 'sheet'
  if (['ppt', 'pptx'].includes(ext)) return 'slide'
  if (['md', 'markdown'].includes(ext)) return 'md'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image'
  if (['json', 'jsonl', 'yaml', 'yml', 'toml', 'ini', 'xml'].includes(ext)) return 'config'
  if (['py', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'java', 'go', 'rs', 'c', 'h', 'cpp', 'hpp', 'cs', 'rb', 'php', 'kt', 'swift', 'sh', 'sql', 'vue', 'css', 'scss'].includes(ext)) return 'code'
  return 'text'
}
