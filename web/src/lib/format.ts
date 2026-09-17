export function ago(ms: number | null | undefined, now = Date.now()): string {
  if (!ms) return ''
  const s = Math.max(0, Math.round((now - ms) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} h`
  return `${Math.round(h / 24)} d`
}

export function shortCwd(cwd: string | null | undefined): string {
  return (cwd ?? '').replace(/^\/home\/[^/]+/, '~')
}

export function statusLabel(s: string, waitingFor = '', goal = false): string {
  if (goal && s === 'busy') return 'working · pursuing a goal'
  if (goal && s === 'idle') return 'your turn · goal still open'
  switch (s) {
    case 'waiting':
      return waitingFor ? `waiting for you: ${waitingFor}` : 'waiting for you'
    case 'busy':
      return 'working'
    case 'idle':
      return 'your turn'
    case 'done':
      return 'finished'
    case 'offline':
      return 'offline'
    default:
      return s
  }
}

export function clock(ms: number | null | undefined): string {
  if (!ms) return ''
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

import type { Session } from './types'

/** "Fable 5.1", "gpt-6-astra": the name people use, without vendor prefixes or date stamps. */
export function modelLabel(s: Session): string {
  const x = s.extra as Record<string, any> | undefined
  if (x?.model_name) return String(x.model_name)
  const id = (s.model || '').replace(/^.*\//, '').replace(/^claude-/, '').replace(/-\d{8}$/, '')
  const m = /^(fable|mythos|opus|sonnet|haiku)-(\d+)(?:-(\d+))?$/.exec(id)
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? `.${m[3]}` : ''}` : id
}

/** Harness plus where the tokens are billed: "claude · max · personal", "claude via openrouter". */
export function backendLabel(s: Session): string {
  const x = s.extra as Record<string, any> | undefined
  if (s.harness === 'tmux') return `${s.provider} (tmux)`
  const std = ['anthropic', 'openai', '']
  let out: string = s.harness
  if (x?.account) out += ` · ${x.account}`
  else if (s.provider && !std.includes(s.provider)) out += ` via ${s.provider}`
  return out
}

export function contextOf(s: Session): { pct: number; window: string } | null {
  const x = s.extra as Record<string, any> | undefined
  if (typeof x?.context_pct !== 'number') return null
  const w = Number(x.context_window) || 0
  return { pct: x.context_pct, window: w >= 1_000_000 ? `${w / 1_000_000}M` : w ? `${Math.round(w / 1000)}k` : '' }
}

/** The generated title when there is one (what the work is about), else the harness' own name. */
export function displayName(s: Session | undefined, fallbackKey = ''): string {
  if (!s) return fallbackKey.split(':').pop()?.slice(0, 8) ?? ''
  const title = (s.extra as Record<string, any> | undefined)?.title
  return (typeof title === 'string' && title) || s.name || s.session_id.slice(0, 8)
}
