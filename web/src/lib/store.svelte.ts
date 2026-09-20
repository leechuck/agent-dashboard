import { api, subscribe } from './api'
import type { BusEvent, Catalog, Cockpit, SlashCommand, Decision, Machine, Message, Session, UsageWindow } from './types'

export type MoveProgress = { key: string; stage: string; phase: string; completed?: number; total?: number }

export class Fleet {
  moves = $state<Record<string, MoveProgress>>({})
  machines = $state<Record<string, Machine>>({})
  sessions = $state<Record<string, Session>>({})
  messages = $state<Record<string, Message[]>>({})
  decisions = $state<Record<string, Decision>>({})
  usage = $state<UsageWindow[]>([])
  cockpit = $state<Cockpit | null>(null)
  catalog = $state<Catalog | null>(null)
  private catalogAt = 0

  /** What each machine can run. Probing endpoints takes seconds, so it is kept for a while. */
  async loadCatalog(fresh = false) {
    if (!fresh && this.catalog && Date.now() - this.catalogAt < 120000) return
    try {
      this.catalog = await api.catalog(fresh)
      this.catalogAt = Date.now()
    } catch {
      /* forms fall back to plain defaults */
    }
  }
  /** Prompt drafts handed from the cockpit to a session's composer, by session key. */
  drafts = $state<Record<string, string>>(loadDrafts())

  setDraft(key: string, text: string) {
    if (text) this.drafts[key] = text
    else delete this.drafts[key]
    try {
      localStorage.setItem('drafts', JSON.stringify(this.drafts))
    } catch {
      /* private window or full storage: drafts then live for this page only */
    }
  }
  connected = $state(false)
  loaded = $state(false)
  error = $state('')
  private stop: (() => void) | null = null

  async load() {
    try {
      const [ms, ss, ds, us] = await Promise.all([api.machines(), api.sessions(), api.decisions(), api.usage()])
      this.machines = Object.fromEntries(ms.map((m) => [m.id, m]))
      this.sessions = Object.fromEntries(ss.map((s) => [s.key, s]))
      this.decisions = Object.fromEntries(ds.map((d) => [d.id, d]))
      this.usage = us
      this.loaded = true
      this.prefetch()
      this.error = ''
    } catch (e) {
      this.error = String(e)
    }
    void this.loadCockpit(false)
    this.stop?.()
    this.stop = subscribe((e) => this.apply(e), (open) => (this.connected = open))
  }

  apply(e: BusEvent) {
    if (e.kind === 'move.progress') {
      const progress = e.data as MoveProgress
      this.moves[progress.key] = progress
    } else if (e.kind === 'session.updated') {
      const s = e.data as Session
      this.sessions[s.key] = s
    } else if (e.kind === 'machine.updated') {
      const m = e.data as Partial<Machine> & { id: string }
      this.machines[m.id] = { ...(this.machines[m.id] ?? emptyMachine(m.id)), ...m }
    } else if (e.kind === 'session.removed') {
      delete this.sessions[(e.data as { key: string }).key]
    } else if (e.kind === 'decision.updated') {
      const d = e.data as Decision
      this.decisions[d.id] = d
    } else if (e.kind === 'usage.updated') {
      const incoming = e.data as UsageWindow[]
      const keep = this.usage.filter((u) => !incoming.some((n) => n.provider === u.provider && n.account === u.account && n.window === u.window))
      this.usage = [...keep, ...incoming].sort((a, b) => a.provider.localeCompare(b.provider) || a.account.localeCompare(b.account) || a.window.localeCompare(b.window))
    } else if (e.kind === 'cockpit.updated') {
      void this.loadCockpit(false)
    } else if (e.kind === 'session.messages') {
      const { session_key, messages, reset } = e.data as { session_key: string; messages: Message[]; reset: boolean }
      if (!this.messages[session_key]) {
        // being fetched right now: keep what arrives meanwhile, it is merged after the fetch
        if (this.inflight.has(session_key) && !reset) (this.early[session_key] ??= []).push(...messages)
        return // otherwise not held here: fetched whole when it is opened
      }
      if (reset) {
        // the hub replaced its copy (node reconnected): take the fresh one
        delete this.messages[session_key]
        void this.openSession(session_key)
      } else {
        const have = new Set(this.messages[session_key].map((m) => m.id))
        const fresh = messages.filter((m) => !have.has(m.id))
        const delivered = new Set(fresh.filter((m) => m.role === 'user' && m.sender && !m.pending).map((m) => m.text.trim()))
        const kept = delivered.size ? this.messages[session_key].filter((m) => !(m.pending && delivered.has(m.text.trim()))) : this.messages[session_key]
        this.messages[session_key] = [...kept, ...fresh].slice(-2000)
      }
    }
  }

  /** Rules and the last advice. Never triggers a model call. */
  async loadCockpit(_unused?: boolean) {
    try {
      this.cockpit = await api.cockpit()
    } catch {
      /* the page shows the last good state */
    }
  }

  async refreshBriefing() {
    if (this.cockpit) this.cockpit.generating = true
    try {
      const r = await api.cockpitBrief()
      if (this.cockpit) Object.assign(this.cockpit, r)
    } finally {
      await this.loadCockpit(false)
    }
  }

  /** Slash commands per session, fetched once when a composer first sees a slash. */
  commands = $state<Record<string, SlashCommand[]>>({})
  private commandsInflight = new Set<string>()

  async loadCommands(key: string) {
    if (this.commands[key] || this.commandsInflight.has(key)) return
    this.commandsInflight.add(key)
    try {
      const r = await api.commands(key)
      if (r.ok) this.commands[key] = r.commands
    } catch {
      /* no completion then; typing still works */
    } finally {
      this.commandsInflight.delete(key)
    }
  }

  /** A session opened by key that the roster never listed (one that is over, from the
      past-sessions list or an old link): fetched once and kept like the others. */
  async ensureSession(key: string) {
    if (this.sessions[key]) return
    try {
      const s = await api.session(key)
      if (!this.sessions[key]) this.sessions[key] = s
    } catch {
      /* the view says it is unknown */
    }
  }

  /** Session keys whose transcript is being fetched right now. */
  loadingMessages = $state<Record<string, boolean>>({})
  private inflight = new Map<string, Promise<void>>()
  private early: Record<string, Message[]> = {}

  /** Fetch a transcript once and keep it; live updates arrive over the event stream.
      Safe to call early (hover, idle prefetch): concurrent calls share one request. */
  openSession(key: string): Promise<void> {
    if (this.messages[key]) return Promise.resolve()
    const running = this.inflight.get(key)
    if (running) return running
    this.loadingMessages[key] = true
    const p = (async () => {
      try {
        for (let attempt = 0; attempt < 3; attempt++) {
          const msgs = await api.messages(key)
          if (msgs.length || this.messages[key]?.length) {
            if (!this.messages[key]?.length) this.messages[key] = msgs
            return
          }
          if (attempt < 2) await new Promise((r) => setTimeout(r, 1500))
        }
        this.messages[key] ??= []
      } catch {
        /* the view offers a retry by reopening */
      } finally {
        const late = this.early[key]
        delete this.early[key]
        if (late?.length && this.messages[key]) {
          const have = new Set(this.messages[key].map((m) => m.id))
          this.messages[key] = [...this.messages[key], ...late.filter((m) => !have.has(m.id))]
        }
        this.inflight.delete(key)
        delete this.loadingMessages[key]
      }
    })()
    this.inflight.set(key, p)
    return p
  }

  /** Warm the transcripts someone is likely to open, one after another, when the page is idle. */
  prefetch(limit = 8) {
    const keys = this.sessionList
      .filter((s) => ['busy', 'idle', 'waiting'].includes(s.status) && !Fleet.isStale(s) && !(s.extra as any)?.parent && s.transcript_path)
      .slice(0, limit)
      .map((s) => s.key)
    const next = async () => {
      const key = keys.shift()
      if (!key) return
      await this.openSession(key)
      setTimeout(next, 150)
    }
    const idle = (window as any).requestIdleCallback as ((cb: () => void) => void) | undefined
    if (idle) idle(next)
    else setTimeout(next, 1200)
  }

  async fullMessage(key: string, id: string) {
    const full = await api.message(key, id)
    const list = this.messages[key]
    const i = list?.findIndex((m) => m.id === id) ?? -1
    if (list && i >= 0) list[i] = full
    return full
  }

  /** Whether the dashboard itself can deliver a prompt (Claude inbox socket, pi extension). */
  static canSend(s: Session | undefined): boolean {
    if (!s || s.status === 'offline') return false
    const x = s.extra as Record<string, unknown> | undefined
    return (s.harness === 'claude' && !!x?.socket) || (s.harness === 'pi' && !!x?.inbox) || !!x?.tmux
  }

  /** Slash commands only work typed into the terminal, so the session must sit in tmux. */
  static canDeliver(s: Session | undefined, text: string): boolean {
    if (!Fleet.canSend(s)) return false
    return !text.trim().startsWith('/') || !!(s!.extra as Record<string, unknown> | undefined)?.tmux
  }

  /** Not busy and untouched for two days: hidden by default, removable in bulk. */
  static isStale(s: Session, now = Date.now()): boolean {
    return s.status !== 'busy' && now - s.updated_at > 48 * 3600 * 1000
  }

  async cleanup(machine: string, keys?: string[]) {
    return api.cleanup(machine, keys)
  }

  get sessionList(): Session[] {
    return Object.values(this.sessions).sort(bySeverity)
  }

  /** Sessions that need Robert now: waiting and touched within two days. */
  get waiting(): Session[] {
    const cutoff = Date.now() - 48 * 3600 * 1000
    return this.sessionList.filter((s) => s.status === 'waiting' && s.updated_at > cutoff)
  }

  /** The most-used subscription window (session/week), for the header badge. Money is not a window. */
  get worstUsage(): UsageWindow | null {
    return this.usage
      .filter((u) => (u.provider === 'anthropic' || u.provider === 'openai') && u.window !== 'extra_usage')
      .reduce<UsageWindow | null>((w, u) => (!w || u.used_pct > w.used_pct ? u : w), null)
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

function loadDrafts(): Record<string, string> {
  try {
    const d = JSON.parse(localStorage.getItem('drafts') ?? '{}')
    return d && typeof d === 'object' ? d : {}
  } catch {
    return {}
  }
}

function emptyMachine(id: string): Machine {
  return { id, hostname: id, os: '', harnesses: [], node_version: '', online: false, armed: false, armed_until: 0, last_seen: 0 }
}

// what needs the owner comes first: blocked on a question, then finished and waiting for
// the next instruction; agents that are working need nothing
const rank: Record<string, number> = { waiting: 0, idle: 1, busy: 2, done: 3, stopped: 4, failed: 5, offline: 6 }
function bySeverity(a: Session, b: Session): number {
  const r = (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
  return r !== 0 ? r : b.updated_at - a.updated_at
}

export const fleet = new Fleet()
