export type Status = 'busy' | 'idle' | 'waiting' | 'done' | 'failed' | 'stopped' | 'offline'

export interface Session {
  key: string
  machine: string
  harness: 'claude' | 'codex' | 'pi' | 'opencode' | 'hermes' | 'tmux'
  provider: string
  session_id: string
  name: string
  cwd: string
  kind: 'interactive' | 'background' | 'headless' | 'unknown'
  status: Status
  waiting_for: string
  pid: number | null
  started_at: number | null
  updated_at: number
  transcript_path: string
  native_url: string
  last_line: string
  model: string
  extra: Record<string, unknown>
}

export interface Machine {
  id: string
  hostname: string
  os: string
  harnesses: string[]
  node_version: string
  online: boolean
  armed: boolean
  armed_until: number
  last_seen: number
}

export type DecisionStatus = 'pending' | 'allowed' | 'denied' | 'expired' | 'cancelled' | 'answered'

export interface Decision {
  id: string
  machine: string
  session_key: string
  harness: string
  kind: 'permission' | 'approval' | 'question'
  tool_name: string
  tool_input: Record<string, unknown> | null
  question: string
  reason: string
  cwd: string
  session_name: string
  native_url: string
  created_at: number
  expires_at: number
  status: DecisionStatus
  answered_at: number | null
  answer_reason: string
  remember: boolean
}

export interface Message {
  id: string
  ts: number | null
  role: 'user' | 'assistant' | 'tool' | 'system'
  kind: 'text' | 'thinking' | 'tool_use' | 'tool_result' | 'system'
  text: string
  tool_name: string
  tool_use_id: string
  tool_input: Record<string, unknown> | null
  is_error: boolean
  is_meta: boolean
  agent_id: string
}

export interface BusEvent {
  kind: string
  data: any
}

export interface UsageWindow {
  provider: string
  account: string
  window: string
  label: string
  used_pct: number
  resets_at: number | null
  source: string
  machine: string
  fetched_at: number
  detail: Record<string, any>
}

export interface CockpitAction {
  type: 'open_session' | 'open_decisions' | 'open_limits' | 'new_session' | 'cleanup' | 'send_prompt'
  label: string
  href: string
  machine: string
  keys: string[]
  prompt: string
}

export interface Finding {
  id: string
  severity: 'act' | 'warn' | 'info'
  kind: string
  title: string
  detail: string
  session_key: string
  machine: string
  action: CockpitAction | null
}

export interface Suggestion {
  id: string
  title: string
  why: string
  kind: string
  session_key: string
  prompt: string
}

export interface Briefing {
  summary: string
  sessions: Record<string, string>
  suggestions: Suggestion[]
  generated_at: number
  model: string
  via: string
  cost_usd: number | null
}

export interface Cockpit {
  headline: string
  stats: { busy: number; waiting: number; idle: number; stale: number; subagents: number; machines_online: number; machines: number }
  findings: Finding[]
  headroom: { provider: string; account: string; label: string; used_pct: number; window: string; command: string }[]
  silenced: { id: string; title: string; until: number }[]
  briefing: Briefing | null
  outdated: boolean
  generating: boolean
  error: string
}

export interface PADraft {
  kind: 'email_reply' | 'email_new' | 'mattermost' | 'whatsapp'
  buffer?: string
  message_id?: string
  wide?: boolean
  to?: string
  cc?: string
  subject?: string
  body: string
}

export interface PATask {
  title: string
  workspace?: string
  machine?: string
  path?: string
  harness?: string
  prompt: string
  deadline?: string
}

export interface PAItem {
  id: string
  channel: string
  urgency: 'now' | 'today' | 'week' | 'fyi'
  from?: string
  subject?: string
  received?: string
  ask?: string
  needs_decision?: string
  link?: string
  draft?: PADraft | null
  task?: PATask | null
  status: 'open' | 'done' | 'ignored' | 'sent' | 'delegated'
  status_note: string
}

export interface PAWorkspace {
  name?: string
  kind?: string
  machine?: string
  path: string
  note?: string
}

export interface PAState {
  ok: boolean
  error?: string
  machine?: string
  briefing: {
    generated_at?: string
    file_mtime?: number
    summary?: string
    schedule?: { when: string; what: string; note?: string }[]
    deadlines?: { date: string; what: string; project?: string }[]
    items: PAItem[]
  } | null
  session: { key: string; status: string; name: string } | null
  workspaces: PAWorkspace[]
  roots: PAWorkspace[]
}
