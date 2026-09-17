import type { AgentChoice, AgentSettings, Catalog, Endpoint, SlashCommand, AgentSettingsView, BusEvent, Cockpit, Decision, PAActResult, PAState, Machine, Message, Session, UsageWindow } from './types'

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
  message: (key: string, id: string) => j<Message>(`/api/sessions/${encodeURIComponent(key)}/message?id=${encodeURIComponent(id)}`),
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
  action: (key: string, action: string) =>
    j<{ ok: boolean; error?: string; output?: string }>(`/api/sessions/${encodeURIComponent(key)}/action`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  startSession: (machine: string, spec: Partial<AgentChoice> & { cwd: string; prompt?: string; name?: string; resume?: string; mode?: string }) =>
    j<{ ok: boolean; error?: string; job_id?: string; output?: string; attach?: string; tmux?: { name: string } }>(`/api/machines/${encodeURIComponent(machine)}/sessions`, {
      method: 'POST',
      body: JSON.stringify(spec),
    }),
  commands: (key: string) => j<{ ok: boolean; commands: SlashCommand[]; error?: string }>(`/api/sessions/${encodeURIComponent(key)}/commands`),
  catalog: (fresh = false) => j<Catalog>(`/api/catalog?fresh=${fresh}`),
  saveEndpoints: (endpoints: Endpoint[]) => j<{ ok: boolean }>('/api/settings/endpoints', { method: 'PUT', body: JSON.stringify({ endpoints }) }),
  openLogin: (machine: string, name: string) =>
    j<{ ok: boolean; error?: string; terminal_key?: string; attach?: string }>(`/api/machines/${encodeURIComponent(machine)}/logins`, { method: 'POST', body: JSON.stringify({ name }) }),
  switchSession: (key: string, body: Partial<AgentChoice> & { note?: string; force?: boolean; stop_old?: boolean; cwd?: string }) =>
    j<{ ok: boolean; error?: string; resumed?: boolean; attach?: string; terminal_key?: string; session_key?: string }>(`/api/sessions/${encodeURIComponent(key)}/switch`, { method: 'POST', body: JSON.stringify(body) }),
  past: (params: { machine?: string; harness?: string; q?: string; limit?: number } = {}) =>
    j<{ sessions: Session[]; errors: Record<string, string> }>(
      `/api/past?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])))}`,
    ),
  moveSession: (key: string, body: Partial<AgentChoice> & { machine: string; cwd?: string; note?: string; stop_old?: boolean; force?: boolean; name?: string }) =>
    j<{ ok: boolean; error?: string; attach?: string; terminal_key?: string; session_key?: string; stopped?: boolean; source?: string }>(`/api/sessions/${encodeURIComponent(key)}/move`, { method: 'POST', body: JSON.stringify(body) }),
  setTitle: (key: string, title: string) => j<{ ok: boolean; title: string }>(`/api/sessions/${encodeURIComponent(key)}/title`, { method: 'PUT', body: JSON.stringify({ title }) }),
  regenerateTitle: (key: string) => j<{ ok: boolean; title: string }>(`/api/sessions/${encodeURIComponent(key)}/title/regenerate`, { method: 'POST', body: '{}' }),
  regenerateTitles: () => j<{ ok: boolean; renamed: number }>('/api/titles/regenerate', { method: 'POST', body: '{}' }),
  cleanup: (machine: string, keys?: string[]) =>
    j<{ removed: number; results: { key: string; ok: boolean; error?: string }[] }>(`/api/machines/${encodeURIComponent(machine)}/cleanup`, {
      method: 'POST',
      body: JSON.stringify({ older_than_hours: 48, keys }),
    }),
  dirs: (machine: string) => j<string[]>(`/api/machines/${encodeURIComponent(machine)}/dirs`),
  cockpit: () => j<Cockpit>('/api/cockpit'),
  cockpitBrief: () => j<Pick<Cockpit, 'briefing' | 'outdated' | 'generating' | 'error'>>('/api/cockpit/brief', { method: 'POST', body: '{}' }),
  silence: (id: string, title: string, hours: number | null) =>
    j<{ ok: boolean }>('/api/cockpit/silence', { method: 'POST', body: JSON.stringify({ id, title, hours }) }),
  unsilence: (id: string) => j<{ ok: boolean }>(`/api/cockpit/silence/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  agentSettings: () => j<AgentSettingsView>('/api/settings/agents'),
  saveAgentSettings: (agents: AgentSettings) => j<{ ok: boolean; agents: AgentSettings }>('/api/settings/agents', { method: 'PUT', body: JSON.stringify(agents) }),
  pa: () => j<PAState>('/api/pa'),
  paRun: (focus = '') => j<{ ok: boolean; error?: string; already_running?: boolean }>('/api/pa/run', { method: 'POST', body: JSON.stringify({ focus }) }),
  paPanel: <T>(name: string, fresh = false) => j<T>(`/api/pa/panel/${name}${fresh ? '?fresh=true' : ''}`),
  paAct: (act: string, args: Record<string, unknown> = {}) => j<PAActResult>('/api/pa/act', { method: 'POST', body: JSON.stringify({ act, args }) }),
  paItem: (id: string, op: 'send_email' | 'discard' | 'mark', extra: { body?: string | null; status?: string; note?: string } = {}) =>
    j<{ ok: boolean; error?: string; status?: string }>('/api/pa/item', { method: 'POST', body: JSON.stringify({ id, op, ...extra }) }),
  usage: () => j<UsageWindow[]>('/api/usage'),
  usageHistory: (provider: string, window: string, account: string, hours = 48) =>
    j<{ t: number; pct: number }[]>(`/api/usage/history?${new URLSearchParams({ provider, window, account, hours: String(hours) })}`),
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
