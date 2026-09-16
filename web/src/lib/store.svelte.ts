import { api, subscribe } from './api'
import type { BusEvent, Decision, Machine, Message, Session } from './types'

class Fleet {
  machines = $state<Record<string, Machine>>({})
  sessions = $state<Record<string, Session>>({})
  messages = $state<Record<string, Message[]>>({})
  decisions = $state<Record<string, Decision>>({})
  connected = $state(false)
  loaded = $state(false)
  error = $state('')
  private stop: (() => void) | null = null

  async load() {
    try {
      const [ms, ss, ds] = await Promise.all([api.machines(), api.sessions(), api.decisions()])
      this.machines = Object.fromEntries(ms.map((m) => [m.id, m]))
      this.sessions = Object.fromEntries(ss.map((s) => [s.key, s]))
      this.decisions = Object.fromEntries(ds.map((d) => [d.id, d]))
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
    } else if (e.kind === 'decision.updated') {
      const d = e.data as Decision
      this.decisions[d.id] = d
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

  /** Sessions that need Robert now: waiting and touched within two days. */
  get waiting(): Session[] {
    const cutoff = Date.now() - 48 * 3600 * 1000
    return this.sessionList.filter((s) => s.status === 'waiting' && s.updated_at > cutoff)
  }

  get pendingDecisions(): Decision[] {
    return Object.values(this.decisions)
      .filter((d) => d.status === 'pending')
      .sort((a, b) => a.created_at - b.created_at)
  }

  get recentDecisions(): Decision[] {
    return Object.values(this.decisions)
      .filter((d) => d.status !== 'pending')
      .sort((a, b) => (b.answered_at ?? b.created_at) - (a.answered_at ?? a.created_at))
      .slice(0, 50)
  }

  async answer(id: string, behavior: 'allow' | 'deny', remember = false) {
    const r = await api.answer(id, behavior, remember)
    if (!r.ok) throw new Error(r.error ?? 'failed')
  }

  async arm(machine: string, armed: boolean) {
    const r = await api.arm(machine, armed)
    this.machines[machine] = { ...this.machines[machine], ...r }
  }
}

function emptyMachine(id: string): Machine {
  return { id, hostname: id, os: '', harnesses: [], node_version: '', online: false, armed: false, armed_until: 0, last_seen: 0 }
}

const rank: Record<string, number> = { waiting: 0, busy: 1, idle: 2, done: 3, stopped: 4, failed: 5, offline: 6 }
function bySeverity(a: Session, b: Session): number {
  const r = (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
  return r !== 0 ? r : b.updated_at - a.updated_at
}

export const fleet = new Fleet()
