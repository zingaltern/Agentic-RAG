/**
 * 轻量 Markdown 解析（只产出结构化 token，绝不生成 HTML 字符串）。
 * 模板用插值渲染 token，天然转义 —— 与项目 S3「禁 innerHTML」一致。
 *
 * 支持：标题 / 无序列表 / 有序列表 / 引用 / 代码块 / 分隔线 / 段落，
 * 行内：**粗体**、`行内代码`、[n] 引用标号（问答回答里的溯源角标）。
 */

const CITATION = /^\[\d+\]$/

/** 行内切分：代码 > 粗体 > 引用标号；其余为纯文本（交给模板转义）。 */
export function parseInline(text) {
  const src = String(text ?? '')
  if (!src) return []
  const out = []
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[\d+\])/g
  let last = 0
  let m
  while ((m = re.exec(src)) !== null) {
    if (m.index > last) out.push({ type: 'text', text: src.slice(last, m.index) })
    const token = m[0]
    if (token.startsWith('`')) out.push({ type: 'code', text: token.slice(1, -1) })
    else if (token.startsWith('**')) out.push({ type: 'strong', text: token.slice(2, -2) })
    else if (CITATION.test(token)) out.push({ type: 'cite', text: token })
    else out.push({ type: 'text', text: token })
    last = m.index + token.length
  }
  if (last < src.length) out.push({ type: 'text', text: src.slice(last) })
  return out
}

/**
 * 按块解析。返回 [{type, text?, items?, lang?}]
 * 解析失败/未知语法一律退化为段落，保证「永远有东西可渲染」。
 */
export function parseMarkdown(source) {
  const src = String(source ?? '').replace(/\r\n?/g, '\n')
  if (!src.trim()) return []
  const lines = src.split('\n')
  const blocks = []
  let i = 0

  const flushParagraph = (buf) => {
    const text = buf.join(' ').trim()
    if (text) blocks.push({ type: 'p', text })
  }

  let para = []
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // 代码块
    if (trimmed.startsWith('```')) {
      flushParagraph(para); para = []
      const lang = trimmed.slice(3).trim()
      const body = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i]); i += 1
      }
      i += 1 // 跳过结束围栏（缺失时自然结束）
      blocks.push({ type: 'code', lang, text: body.join('\n') })
      continue
    }

    // 空行 → 段落边界
    if (!trimmed) {
      flushParagraph(para); para = []
      i += 1
      continue
    }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph(para); para = []
      blocks.push({ type: 'hr' })
      i += 1
      continue
    }

    // 标题
    const h = trimmed.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      flushParagraph(para); para = []
      blocks.push({ type: `h${h[1].length}`, text: h[2].trim() })
      i += 1
      continue
    }

    // 引用
    if (trimmed.startsWith('>')) {
      flushParagraph(para); para = []
      const body = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        body.push(lines[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push({ type: 'quote', text: body.join(' ').trim() })
      continue
    }

    // 无序列表
    if (/^[-*•]\s+/.test(trimmed)) {
      flushParagraph(para); para = []
      const items = []
      while (i < lines.length && /^[-*•]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*•]\s+/, ''))
        i += 1
      }
      blocks.push({ type: 'ul', items })
      continue
    }

    // 有序列表（1. / 1) ）
    if (/^\d+[.)]\s+/.test(trimmed)) {
      flushParagraph(para); para = []
      const items = []
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[.)]\s+/, ''))
        i += 1
      }
      blocks.push({ type: 'ol', items })
      continue
    }

    para.push(trimmed)
    i += 1
  }
  flushParagraph(para)
  return blocks
}

/** 把回答文本切成「段落块」（供模板渲染），空文本返回空数组。 */
export function toBlocks(text) {
  return parseMarkdown(text)
}
