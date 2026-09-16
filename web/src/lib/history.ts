export interface HistorySession {
  id: string
  project: string
  machine: string
  agent: string
  first_message: string
  started_at: string
  ended_at: string
  message_count: number
  user_message_count: number
  cwd: string
  source_session_id: string
  health_grade: string
  outcome: string
}

export interface HistoryMessage {
  id: string
  ordinal: number
  role: string
  content: string
  thinking_text: string
  timestamp: string
  has_tool_use: boolean
  is_system: boolean
  source_type: string
}

async function h<T>(path: string): Promise<T> {
  const r = await fetch(`/history${path}`)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const history = {
  sessions: (params: Record<string, string> = {}) =>
    h<{ sessions: HistorySession[]; next_cursor: string; total: number }>(
      `/api/v1/sessions?${new URLSearchParams({ limit: '40', ...params })}`,
    ),
  search: (q: string) =>
    h<{ results?: (HistorySession & { session_id?: string; name?: string })[] }>(
      `/api/v1/search?${new URLSearchParams({ q, limit: '40' })}`,
    ),
  machines: () => h<{ machines: string[] }>('/api/v1/machines'),
  messages: (id: string) =>
    h<{ messages: HistoryMessage[]; count: number }>(
      `/api/v1/sessions/${encodeURIComponent(id)}/messages?limit=300`,
    ),
  session: (id: string) => h<HistorySession>(`/api/v1/sessions/${encodeURIComponent(id)}`),
}

export function resumeCommand(s: HistorySession): string {
  const id = s.source_session_id || s.id
  switch (s.agent) {
    case 'claude':
      return `cd ${s.cwd} && claude --resume ${id}`
    case 'codex':
      return `cd ${s.cwd} && codex resume ${id}`
    case 'pi':
      return `cd ${s.cwd} && pi -r`
    case 'opencode':
      return `cd ${s.cwd} && opencode -s ${id}`
    default:
      return ''
  }
}
