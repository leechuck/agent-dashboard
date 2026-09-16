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

export function shortCwd(cwd: string): string {
  return cwd.replace(/^\/home\/[^/]+/, '~')
}

export function statusLabel(s: string, waitingFor = ''): string {
  switch (s) {
    case 'waiting':
      return waitingFor ? `waiting for you: ${waitingFor}` : 'waiting for you'
    case 'busy':
      return 'busy'
    case 'idle':
      return 'idle'
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
