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
  /** set when it did not come from the terminal: "dashboard", or a peer session */
  sender?: string
  /** long tool input or output was cut for the list; the full message is fetched on open */
  slim?: boolean
  /** sent from the dashboard into the agent's inbox; it has not read it yet */
  pending?: boolean
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
    report?: string
    schedule?: { when: string; what: string; note?: string }[]
    deadlines?: { date: string; what: string; project?: string }[]
    items: PAItem[]
  } | null
  session: { key: string; status: string; name: string } | null
  workspaces: PAWorkspace[]
  roots: PAWorkspace[]
}

/** One open item of the todo lists kept in the PA repo (docs/pa-panels.md). */
export interface PATodo {
  id: string
  list: string
  date: string
  status: string
  title: string
  body: string
  snoozed_until: string
  project: string
  project_name: string
  kind: string
  bucket: 'overdue' | 'today' | 'week' | 'later' | 'snoozed' | 'closed'
  days: number
}

export interface PATodoPanel {
  ok: boolean
  error?: string
  today: string
  items: PATodo[]
  lists: string[]
  counts: Record<string, number>
  projects: { slug: string; name: string; kind: string }[]
}

export interface PAEvent {
  id: string
  title: string
  start: string
  end: string
  all_day: boolean
  calendar: string
  location: string
  link: string
  busy: boolean
  guests: number
  unanswered: boolean
  flags: string[]
}

export interface PAAgendaPanel {
  ok: boolean
  error?: string
  warning?: string
  generated: string
  source: string
  today: string
  events: PAEvent[]
  trips: { title: string; start: string; end: string }[]
  flagged: number
  calendars: string[]
}

export interface PAActResult {
  ok: boolean
  error?: string
  warning?: string
  output?: string
  [k: string]: unknown
}

export interface AgentSettings {
  advice: { machine: string; harness: 'claude' | 'codex' | 'api'; model: string; login: string; endpoint: string; effort: string }
  personal: { machine: string; backend: 'login' | 'endpoint'; model: string; login: string; endpoint: string }
  titles: { enabled: boolean; model: string }
}

export interface AgentSettingsView {
  agents: AgentSettings
  machines: string[]
  logins: { dir: string; account: string }[]
}

export interface Endpoint {
  id: string
  name?: string
  base_url?: string
  anthropic_base_url?: string
  key_env?: string
  wire_api?: string
  context_window?: number
  models?: string[]
}

export interface ModelChoice {
  id: string
  label: string
  efforts?: string[]
}

export interface MachineCatalog {
  ok: boolean
  error?: string
  harnesses: Record<string, boolean>
  tmux: boolean
  logins: { dir: string; name: string; account: string; plan: string; logged_in: boolean; expired?: boolean }[]
  models: Record<string, ModelChoice[]>
  efforts: Record<string, string[]>
  endpoints: { id: string; key_present: boolean; reachable: boolean; models: string[]; error: string }[]
}

export interface Catalog {
  endpoints: Endpoint[]
  machines: Record<string, MachineCatalog>
}

/** What to run: harness, where the tokens come from, model and how it may act. */
export interface AgentChoice {
  harness: string
  backend: 'default' | 'login' | 'endpoint'
  login: string
  endpoint: string
  model: string
  effort: string
  permissions: string
  work_mode?: '' | 'plan' | 'implement'
}

export interface SlashCommand {
  name: string
  description: string
  source: 'builtin' | 'user' | 'project' | 'plugin' | 'skill'
  /** hint for what may follow, such as "<model>" */
  args: string
  /** the values the argument may take, when they are known */
  options: { value: string; label: string }[]
  /** anything may follow; only the hint is shown */
  free: boolean
}

export interface WeeklyReports {
  ok: boolean
  error?: string
  week: string
  checked_at: string
  deadline: string
  warning?: string
  members: {
    slug: string; name: string; role: string
    status: 'received' | 'awaiting' | 'overdue' | 'exempt' | 'unknown'
    date?: string; subject?: string; message_id?: string; error?: string
    flags?: string[]; report: string
    followups?: { message_id: string; date: string; body: string; attachments: { file: string; text: string }[] }[]
  }[]
}

export interface GroupReview {
  version?: number
  progress?: string[]
  risks?: string[]
  next_step?: string
  intervention?: string
  state: 'on_track' | 'watch' | 'attention' | 'unknown'
  reason: string
  evidence: string[]
  reviewed_at: string
}
export interface GroupRoster {
  ok: boolean
  error?: string
  members: {
    slug: string; name: string; role: string; state: GroupReview['state']
    review: GroupReview | null; stale: boolean; has_notes: boolean; org_source: string
    latest_report: {week: string; status: string; checked_at?: string} | null
  }[]
}
