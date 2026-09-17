<script lang="ts">
  import { onMount } from 'svelte'
  import { history, resumeCommand, type HistoryMessage, type HistorySession } from '../lib/history'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import { ago, displayName, modelLabel, shortCwd } from '../lib/format'
  import type { Session } from '../lib/types'

  // Past sessions come from the machines themselves (what each harness keeps on disk),
  // so every one of them can be opened, resumed there, or moved to another machine.
  // agentsview, when configured, adds full-text search over the same history.
  let { id }: { id?: string } = $props()
  let q = $state('')
  let harness = $state('')
  let machine = $state('')
  let rows = $state<Session[]>([])
  let errors = $state<Record<string, string>>({})
  let loading = $state(false)
  let error = $state('')
  let mode = $state<'past' | 'text'>('past')
  let publicUrl = $state('')
  let textEnabled = $state(false)
  let textRows = $state<HistorySession[]>([])
  let detail = $state<{ session: HistorySession; messages: HistoryMessage[] } | null>(null)
  let acting = $state<Record<string, string>>({})
  let timer: ReturnType<typeof setTimeout> | undefined

  const machines = $derived(Object.values(fleet.machines).filter((m) => m.online).map((m) => m.id))

  async function load() {
    loading = true
    error = ''
    try {
      if (mode === 'past') {
        const r = await api.past({ machine, harness, q: q.trim(), limit: 150 })
        rows = r.sessions
        errors = r.errors
        for (const s of rows) if (!fleet.sessions[s.key]) fleet.sessions[s.key] = s
      } else if (q.trim()) {
        const r = await history.search(q.trim())
        textRows = (r.results ?? []).map((x) => ({ ...x, id: x.session_id ?? x.id, first_message: x.name ?? x.first_message ?? '' }))
      } else {
        textRows = (await history.sessions(harness ? { agent: harness } : {})).sessions
      }
    } catch (e) {
      error = String(e)
    } finally {
      loading = false
    }
  }

  function onInput() {
    clearTimeout(timer)
    timer = setTimeout(load, 300)
  }

  /** Start the same conversation again on its own machine, in tmux. */
  async function resume(s: Session) {
    acting[s.key] = 'Resuming…'
    try {
      const r = await api.switchSession(s.key, { harness: s.harness })
      acting[s.key] = r.ok ? `Resumed (${r.attach}); it appears on the board shortly.` : r.error ?? 'failed'
      if (r.ok && r.session_key) setTimeout(() => (location.hash = `#/session/${encodeURIComponent(r.session_key!)}`), 800)
    } catch (e) {
      acting[s.key] = String(e)
    }
  }

  $effect(() => {
    if (!id) {
      detail = null
      return
    }
    ;(async () => {
      try {
        const [session, m] = await Promise.all([history.session(id), history.messages(id)])
        detail = { session, messages: m.messages }
      } catch (e) {
        error = String(e)
      }
    })()
  })

  onMount(async () => {
    try {
      const c = await api.config()
      publicUrl = c.history_public_url
      textEnabled = c.history_enabled
    } catch {
      /* optional */
    }
    load()
  })

  function when(ms: number | null | undefined): string {
    if (!ms) return ''
    const d = new Date(ms)
    const sameYear = d.getFullYear() === new Date().getFullYear()
    return d.toLocaleDateString([], { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
  }
  function whenIso(iso: string): string {
    return iso ? when(new Date(iso).getTime()) : ''
  }
  function size(s: Session): string {
    const b = Number((s.extra as any)?.size) || 0
    return b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} kB` : ''
  }
  const resumable = (s: Session) => ['claude', 'codex', 'pi'].includes(s.harness)
</script>

<section>
  {#if detail}
    <div class="dhead">
      <a href="#/history">← History</a>
      <div class="title">{detail.session.first_message || detail.session.id}</div>
      <div class="meta small muted">{detail.session.agent} · {detail.session.machine} · {shortCwd(detail.session.cwd)} · {whenIso(detail.session.started_at)}</div>
      <pre class="mono resume">{resumeCommand(detail.session)}</pre>
      {#if publicUrl}
        <a class="small" href={`${publicUrl}/sessions/${encodeURIComponent(detail.session.id)}`} target="_blank" rel="noopener">Open in agentsview</a>
      {/if}
    </div>
    <div class="msgs">
      {#each detail.messages as m (m.id)}
        {#if !m.is_system && m.content}
          <div class={`msg ${m.role}`}>
            <div class="who small muted">{m.role === 'user' ? 'you' : m.role}{m.has_tool_use ? ' · tools' : ''} {whenIso(m.timestamp)}</div>
            <pre class="text">{m.content}</pre>
          </div>
        {/if}
      {/each}
    </div>
  {:else}
    <div class="top">
      <h1>History</h1>
      {#if textEnabled}
        <div class="modes small">
          <button class:on={mode === 'past'} onclick={() => { mode = 'past'; load() }}>Past sessions</button>
          <button class:on={mode === 'text'} onclick={() => { mode = 'text'; load() }}>Full-text search</button>
        </div>
      {/if}
    </div>
    <div class="filters">
      <input type="search" placeholder={mode === 'past' ? 'Filter by title, request, folder or id' : 'Search the text of all sessions'} bind:value={q} oninput={onInput} />
      <select bind:value={harness} onchange={load}>
        <option value="">any agent</option>
        {#each ['claude', 'codex', 'pi'] as a}<option value={a}>{a}</option>{/each}
      </select>
      {#if mode === 'past' && machines.length > 1}
        <select bind:value={machine} onchange={load}>
          <option value="">every machine</option>
          {#each machines as m}<option value={m}>{m}</option>{/each}
        </select>
      {/if}
      <button onclick={load} disabled={loading} title="Read the machines again">↻</button>
    </div>
    {#if error}<p class="notice err">{error}</p>{/if}
    {#each Object.entries(errors) as [m, e] (m)}<p class="notice err small">{m}: {e}</p>{/each}
    {#if loading && rows.length === 0 && textRows.length === 0}<p class="notice muted">Reading the machines…</p>{/if}

    {#if mode === 'past'}
      {#if !loading && rows.length === 0 && !error}<p class="notice muted">No past session matches.</p>{/if}
      <ul>
        {#each rows as s (s.key)}
          <li>
            <div class="row">
              <a class="main" href={`#/session/${encodeURIComponent(s.key)}`}>
                <div class="l1">
                  <span class="agent">{s.harness}</span>
                  <span class="name">{displayName(s)}</span>
                  <span class="mach muted small">{s.machine}</span>
                  <span class="when muted small" title={new Date(s.updated_at).toLocaleString()}>{ago(s.updated_at)} ago · {when(s.updated_at)}</span>
                </div>
                <div class="l2 small muted">
                  {shortCwd(s.cwd) || '(folder unknown)'}{modelLabel(s) ? ` · ${modelLabel(s)}` : ''}{size(s) ? ` · ${size(s)}` : ''}{(s.extra as any)?.account ? ` · ${(s.extra as any).account}` : ''}
                </div>
                {#if (s.extra as any)?.first_user && (s.extra as any).first_user !== displayName(s)}
                  <div class="l3 small">{(s.extra as any).first_user}</div>
                {/if}
              </a>
              {#if resumable(s)}
                <div class="acts">
                  <button class="primary" disabled={!!acting[s.key]?.startsWith('Resuming')} onclick={() => resume(s)} title={`Resume on ${s.machine} in tmux`}>Resume</button>
                  {#if machines.filter((m) => m !== s.machine).length}
                    <a class="btn" href={`#/session/${encodeURIComponent(s.key)}?move=1`} title="Resume it on another machine">Move…</a>
                  {/if}
                </div>
              {/if}
            </div>
            {#if acting[s.key]}<div class="small note">{acting[s.key]}</div>{/if}
          </li>
        {/each}
      </ul>
    {:else}
      {#if !loading && textRows.length === 0 && !error}<p class="notice muted">No sessions match.</p>{/if}
      <ul>
        {#each textRows as s (s.id)}
          <li>
            <a class="row main" href={`#/history/${encodeURIComponent(s.id)}`}>
              <div class="l1">
                <span class="agent">{s.agent}</span>
                <span class="name">{s.project}</span>
                <span class="mach muted small">{s.machine}</span>
                <span class="when muted small">{whenIso(s.started_at)}</span>
              </div>
              <div class="l3 small">{s.first_message || '(no prompt)'}</div>
              <div class="l2 small muted">{s.message_count} messages · {shortCwd(s.cwd)}</div>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>

<style>
  .top { display: flex; align-items: baseline; gap: 14px; padding: 16px 16px 8px; flex-wrap: wrap; }
  h1 { font-size: 22px; margin: 0; }
  .modes { display: flex; gap: 2px; border: 1px solid var(--hairline); border-radius: var(--radius); overflow: hidden; }
  .modes button { border: 0; border-radius: 0; padding: 4px 10px; background: var(--surface); }
  .modes button.on { background: var(--cobalt-soft); color: var(--cobalt); font-weight: 600; }
  .filters { display: flex; gap: 8px; padding: 0 16px 10px; flex-wrap: wrap; }
  input[type='search'] { flex: 1 1 200px; padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  select { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  .notice { padding: 8px 16px; margin: 0; }
  .err { color: var(--signal); }
  ul { list-style: none; margin: 0; padding: 0; background: var(--surface); }
  li { border-bottom: 1px solid var(--hairline); }
  .row { display: flex; gap: 10px; align-items: flex-start; padding: 10px 16px; }
  .main { display: block; flex: 1; min-width: 0; color: inherit; text-decoration: none; }
  .main:hover .name { text-decoration: underline; }
  .l1 { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
  .agent { font-weight: 600; flex: none; }
  .name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .when { margin-left: auto; flex: none; }
  .l3 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--ink); }
  .acts { display: flex; flex-direction: column; gap: 6px; flex: none; }
  .acts button, .btn { font-size: 13px; padding: 4px 10px; }
  .btn { border: 1px solid var(--hairline); border-radius: var(--radius); color: var(--ink); text-align: center; }
  .note { padding: 0 16px 8px; color: var(--moss); }
  .dhead { position: sticky; top: var(--sticky-top, 48px); background: var(--surface); border-bottom: 1px solid var(--hairline); padding: 10px 16px; display: grid; gap: 4px; }
  .title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .resume { padding: 6px 10px; background: var(--page); border-radius: var(--radius); user-select: all; }
  .msgs { padding: 8px 0 24px; }
  .msg { padding: 8px 16px; }
  .msg.user { background: var(--cobalt-soft); border-left: 3px solid var(--cobalt); margin: 8px 0; }
  .text { font-family: var(--sans); font-size: 15px; }
  @media (min-width: 960px) { .dhead { top: 0; } }
</style>
