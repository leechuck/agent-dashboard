import type { BusEvent, Decision, Machine, Message, Session } from './types'

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } })
  if (r.status === 401) {
    location.hash = '#/login'
    throw new Error('not authorized')
  }
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  config: () => j<{ history_public_url: string; history_enabled: boolean }>('/api/config'),
  machines: () => j<Machine[]>('/api/machines'),
  sessions: (active = false) => j<Session[]>(`/api/sessions?active=${active}`),
  session: (key: string) => j<Session>(`/api/sessions/${encodeURIComponent(key)}`),
  messages: (key: string) => j<Message[]>(`/api/sessions/${encodeURIComponent(key)}/messages`),
  prompt: (key: string, text: string) =>
    j<{ ok: boolean; error?: string }>(`/api/sessions/${encodeURIComponent(key)}/prompt`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  arm: (machine: string, armed: boolean, hours?: number) =>
    j<{ armed: boolean; armed_until: number }>(`/api/machines/${encodeURIComponent(machine)}/arm`, {
      method: 'POST',
      body: JSON.stringify({ armed, hours }),
    }),
  decisions: (pending = false) => j<Decision[]>(`/api/decisions?pending=${pending}`),
  answer: (id: string, behavior: 'allow' | 'deny', remember = false, reason = '') =>
    j<{ ok: boolean; error?: string }>(`/api/decisions/${encodeURIComponent(id)}/answer`, {
      method: 'POST',
      body: JSON.stringify({ behavior, remember, reason }),
    }),
  pushKey: () => j<{ key: string; enabled: boolean }>('/api/push/key'),
  pushSubscribe: (subscription: unknown, label: string) =>
    j<{ ok: boolean }>('/api/push/subscribe', { method: 'POST', body: JSON.stringify({ subscription, label }) }),
  pushUnsubscribe: (subscription: unknown) =>
    j<{ ok: boolean }>('/api/push/unsubscribe', { method: 'POST', body: JSON.stringify({ subscription }) }),
  pushTest: () => j<{ ok: boolean }>('/api/push/test', { method: 'POST', body: '{}' }),
  login: (token: string) => j<{ ok: boolean }>('/login', { method: 'POST', body: JSON.stringify({ token }) }),
}

export function subscribe(onEvent: (e: BusEvent) => void, onState: (open: boolean) => void): () => void {
  let es: EventSource | null = null
  let closed = false
  let retry = 1000
  const open = () => {
    if (closed) return
    es = new EventSource('/api/events')
    es.onopen = () => {
      retry = 1000
      onState(true)
    }
    es.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data))
      } catch {
        /* ignore */
      }
    }
    es.onerror = () => {
      onState(false)
      es?.close()
      setTimeout(open, retry)
      retry = Math.min(retry * 2, 15000)
    }
  }
  open()
  return () => {
    closed = true
    es?.close()
  }
}
