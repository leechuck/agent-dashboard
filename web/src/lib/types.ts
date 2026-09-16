export type Status = 'busy' | 'idle' | 'waiting' | 'done' | 'failed' | 'stopped' | 'offline'

export interface Session {
  key: string
  machine: string
  harness: 'claude' | 'codex' | 'pi' | 'opencode' | 'tmux'
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
  last_seen: number
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
