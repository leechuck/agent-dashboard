// A small Markdown renderer for agent output. Safe by construction: the text is
// HTML-escaped before any markup is added, and only http(s) links become links.
// Covers what coding agents write: headings, lists, tables, quotes, rules, fenced and
// inline code, bold, italic, strikethrough, links.

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const OPEN = '⟦'
const CLOSE = '⟧'
const SLOT = new RegExp(`${OPEN}(\\d+)${CLOSE}`, 'g')

// Explicit paths and filename-like inline code; ordinary identifiers stay code.
const isFile = (s: string) => /^(?:\/?(?:[\w.@~-]+\/)+[\w.@~:+-]*|[\w@~-]+\.[a-zA-Z][\w.-]*(?::\d+(?::\d+)?)?)$/.test(s)

function inline(raw: string): string {
  // inline code first, so nothing inside it is touched
  const codes: string[] = []
  let s = raw.replace(/`([^`\n]+)`/g, (_, c) => `${OPEN}${codes.push(c) - 1}${CLOSE}`)
  s = esc(s)
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`)
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`)
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, '$1<em>$2</em>').replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?!\w)/g, '$1<em>$2</em>')
  s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
  return s.replace(SLOT, (whole, i) => {
    const code = codes[Number(i)]
    return code === undefined ? whole : `<code${isFile(code) ? ' class="file-path"' : ''}>${esc(code)}</code>`
  })
}

const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())

export function renderMarkdown(src: string): string {
  const lines = src.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`)
    para = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = /^\s*(```|~~~)\s*([\w+-]*)/.exec(line)
    if (fence) {
      flush()
      const body: string[] = []
      for (i++; i < lines.length && !lines[i].trimStart().startsWith(fence[1]); i++) body.push(lines[i])
      const lang = fence[2]
      out.push(`<pre class="code${lang === 'diff' ? ' diff' : ''}"${lang ? ` data-lang="${esc(lang)}"` : ''}><code>${lang === 'diff' ? diffLines(body) : esc(body.join('\n'))}</code></pre>`)
      continue
    }
    if (!line.trim()) {
      flush()
      continue
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      flush()
      const n = Math.min(6, h[1].length + 2) // agent headings sit inside a message: keep them small
      out.push(`<h${n}>${inline(h[2].replace(/\s+#+\s*$/, ''))}</h${n}>`)
      continue
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush()
      out.push('<hr>')
      continue
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush()
      const head = cells(line)
      const rows: string[][] = []
      for (i += 2; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(cells(lines[i]))
      i--
      out.push(`<div class="tablewrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`)
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      flush()
      const q: string[] = []
      for (; i < lines.length && /^\s*>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^\s*>\s?/, ''))
      i--
      out.push(`<blockquote>${q.map(inline).join('<br>')}</blockquote>`)
      continue
    }
    const li = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (li) {
      flush()
      // one flat list per run; deeper items are indented with a class instead of nesting
      const ordered = /\d/.test(li[2])
      const items: string[] = []
      for (; i < lines.length; i++) {
        const m = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i])
        if (m) {
          const depth = Math.min(3, Math.floor(m[1].replace(/\t/g, '  ').length / 2))
          const task = /^\[([ xX])\]\s+(.*)$/.exec(m[3])
          items.push(`<li class="d${depth}${task ? ' task' : ''}">${task ? `<span class="box">${task[1] === ' ' ? '☐' : '☑'}</span> ${inline(task[2])}` : inline(m[3])}`)
        } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
          items[items.length - 1] += `<br>${inline(lines[i].trim())}`
        } else break
      }
      i--
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.map((x) => `${x}</li>`).join('')}</${ordered ? 'ol' : 'ul'}>`)
      continue
    }
    para.push(line)
  }
  flush()
  return out.join('')
}

/** Lines of a unified diff, coloured like a terminal shows them. */
export function diffLines(lines: string[]): string {
  return lines
    .map((l) => {
      const cls = l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('@@') ? 'hunk' : ''
      return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l)
    })
    .join('\n')
}

/** An Edit call as the before/after lines it changes. */
export function editDiff(oldText: string, newText: string): string {
  return diffLines([...oldText.split('\n').map((l) => `- ${l}`), ...newText.split('\n').map((l) => `+ ${l}`)])
}
