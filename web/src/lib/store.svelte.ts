import { api, subscribe } from './api'
import type { BusEvent, Machine, Message, Session } from './types'

class Fleet {
  machines = $state<Record<string, Machine>>({})
  sessions = $state<Record<string, Session>>({})
  messages = $state<Record<string, Message[]>>({})
  connected = $state(false)
  loaded = $state(false)
  error = $state('')
  private stop: (() => void) | null = null

  async load() {
    try {
      const [ms, ss] = await Promise.all([api.machines(), api.sessions()])
      this.machines = Object.fromEntries(ms.map((m) => [m.id, m]))
      this.sessions = Object.fromEntries(ss.map((s) => [s.key, s]))
      this.loaded = true
      this.error = ''
    } catch (e) {
      this.error = String(e)
    }
    this.stop?.()
    this.stop = subscribe((e) => this.apply(e), (open) => (this.connected = open))
  }

  apply(e: BusEvent) {
    if (e.kind === 'session.updated') {
      const s = e.data as Session
      this.sessions[s.key] = s
    } else if (e.kind === 'machine.updated') {
      const m = e.data as Partial<Machine> & { id: string }
      this.machines[m.id] = { ...(this.machines[m.id] ?? emptyMachine(m.id)), ...m }
    } else if (e.kind === 'session.messages') {
      const { session_key, messages, reset } = e.data as { session_key: string; messages: Message[]; reset: boolean }
      const cur = reset ? [] : (this.messages[session_key] ?? [])
      this.messages[session_key] = [...cur, ...messages].slice(-2000)
    }
  }

  async openSession(key: string) {
    if (!this.messages[key]) {
      const msgs = await api.messages(key)
      if (!this.messages[key]) this.messages[key] = msgs
    }
  }

  get sessionList(): Session[] {
    return Object.values(this.sessions).sort(bySeverity)
  }

  get waiting(): Session[] {
    return this.sessionList.filter((s) => s.status === 'waiting')
  }
}

function emptyMachine(id: string): Machine {
  return { id, hostname: id, os: '', harnesses: [], node_version: '', online: false, armed: false, last_seen: 0 }
}

const rank: Record<string, number> = { waiting: 0, busy: 1, idle: 2, done: 3, stopped: 4, failed: 5, offline: 6 }
function bySeverity(a: Session, b: Session): number {
  const r = (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
  return r !== 0 ? r : b.updated_at - a.updated_at
}

export const fleet = new Fleet()
