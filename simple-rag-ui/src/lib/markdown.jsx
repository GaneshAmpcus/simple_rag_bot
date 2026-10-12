// Small, dependency-free markdown -> React renderer for assistant chat
// messages. Deliberately not a full CommonMark implementation -- just
// the subset this app's models actually produce (bold, italic, inline
// code, fenced code, links, bulleted/numbered lists, blockquotes, ###
// headings, hr, paragraphs). Every node is a real React element built
// with createElement, never dangerouslySetInnerHTML, so there is no
// HTML-injection surface regardless of what the model or a KB
// document puts in the text.

import { createElement as h, Fragment } from 'react'

const SAFE_URL_RE = /^(https?:\/\/|mailto:)/i

function safeHref(url) {
  return SAFE_URL_RE.test(url.trim()) ? url : undefined
}

let keySeed = 0
function nextKey() {
  keySeed += 1
  return `md-${keySeed}`
}

const INLINE_RE = /(`[^`\n]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/g

function parseInline(text) {
  if (!text) return []
  const nodes = []
  let lastIndex = 0
  for (const match of text.matchAll(INLINE_RE)) {
    const [full] = match
    const index = match.index ?? 0
    if (index > lastIndex) nodes.push(text.slice(lastIndex, index))

    if (full.startsWith('`')) {
      nodes.push(h('code', { key: nextKey() }, full.slice(1, -1)))
    } else if (full.startsWith('**') || full.startsWith('__')) {
      nodes.push(h('strong', { key: nextKey() }, ...parseInline(full.slice(2, -2))))
    } else if (full.startsWith('*') || full.startsWith('_')) {
      nodes.push(h('em', { key: nextKey() }, ...parseInline(full.slice(1, -1))))
    } else if (full.startsWith('[')) {
      const closeBracket = full.indexOf(']')
      const label = full.slice(1, closeBracket)
      const url = full.slice(closeBracket + 2, -1)
      const href = safeHref(url)
      nodes.push(
        href
          ? h(
              'a',
              { key: nextKey(), href, target: '_blank', rel: 'noopener noreferrer' },
              ...parseInline(label),
            )
          : label,
      )
    }
    lastIndex = index + full.length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

export function renderMarkdown(source) {
  const lines = (source ?? '').replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim() === '') {
      i += 1
      continue
    }

    // Fenced code block
    const fenceMatch = line.match(/^```(\w*)\s*$/)
    if (fenceMatch) {
      const codeLines = []
      i += 1
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i])
        i += 1
      }
      i += 1 // consume closing fence
      blocks.push(
        h('pre', { key: nextKey() }, h('code', null, codeLines.join('\n'))),
      )
      continue
    }

    // Heading
    const headingMatch = line.match(/^(#{1,3})\s+(.*)$/)
    if (headingMatch) {
      const level = headingMatch[1].length
      blocks.push(h(`h${level}`, { key: nextKey() }, ...parseInline(headingMatch[2])))
      i += 1
      continue
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(h('hr', { key: nextKey() }))
      i += 1
      continue
    }

    // Blockquote
    if (/^>\s?/.test(line)) {
      const quoteLines = []
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push(h('blockquote', { key: nextKey() }, ...parseInline(quoteLines.join(' '))))
      continue
    }

    // Unordered list
    if (/^[-*]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(h('li', { key: nextKey() }, ...parseInline(lines[i].replace(/^[-*]\s+/, ''))))
        i += 1
      }
      blocks.push(h('ul', { key: nextKey() }, ...items))
      continue
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(h('li', { key: nextKey() }, ...parseInline(lines[i].replace(/^\d+\.\s+/, ''))))
        i += 1
      }
      blocks.push(h('ol', { key: nextKey() }, ...items))
      continue
    }

    // Paragraph -- consume consecutive plain lines, soft-break between them
    const paraLines = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,3})\s+/.test(lines[i]) &&
      !/^[-*]\s+/.test(lines[i]) &&
      !/^\d+\.\s+/.test(lines[i]) &&
      !/^>\s?/.test(lines[i]) &&
      !/^(-{3,}|\*{3,})\s*$/.test(lines[i])
    ) {
      paraLines.push(lines[i])
      i += 1
    }
    const paraNodes = []
    paraLines.forEach((l, idx) => {
      if (idx > 0) paraNodes.push(h('br', { key: nextKey() }))
      paraNodes.push(...parseInline(l))
    })
    blocks.push(h('p', { key: nextKey() }, ...paraNodes))
  }

  return h(Fragment, null, ...blocks)
}

export default function Markdown({ children, className = '' }) {
  return h('div', { className: `md-content ${className}`.trim() }, renderMarkdown(children))
}
