<script lang="ts">
  import { onMount } from 'svelte'
  import { history, resumeCommand, type HistoryMessage, type HistorySession } from '../lib/history'
  import { api } from '../lib/api'
  import { shortCwd } from '../lib/format'

  let { id }: { id?: string } = $props()
  let q = $state('')
  let agent = $state('')
  let machine = $state('')
  let machines = $state<string[]>([])
  let rows = $state<HistorySession[]>([])
  let loading = $state(false)
  let error = $state('')
  let detail = $state<{ session: HistorySession; messages: HistoryMessage[] } | null>(null)
  let publicUrl = $state('')
  let timer: ReturnType<typeof setTimeout> | undefined

  async function load() {
    loading = true
    error = ''
    try {
      if (q.trim()) {
        const r = await history.search(q.trim())
        rows = (r.results ?? []).map((x) => ({
          ...x,
          id: x.session_id ?? x.id,
          first_message: x.name ?? x.first_message ?? '',
        }))
      } else {
        const params: Record<string, string> = {}
        if (agent) params.agent = agent
        if (machine) params.machine = machine
        rows = (await history.sessions(params)).sessions
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

  async function open(s: HistorySession) {
    location.hash = `#/history/${encodeURIComponent(s.id)}`
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
      machines = (await history.machines()).machines
    } catch {
      /* optional */
    }
    load()
  })

  function when(iso: string): string {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
</script>

<section>
  {#if detail}
    <div class="dhead">
      <a href="#/history">← History</a>
      <div class="title">{detail.session.first_message || detail.session.id}</div>
      <div class="meta small muted">{detail.session.agent} · {detail.session.machine} · {shortCwd(detail.session.cwd)} · {when(detail.session.started_at)}</div>
      <pre class="mono resume">{resumeCommand(detail.session)}</pre>
      {#if publicUrl}
        <a class="small" href={`${publicUrl}/sessions/${encodeURIComponent(detail.session.id)}`} target="_blank" rel="noopener">Open in agentsview</a>
      {/if}
    </div>
    <div class="msgs">
      {#each detail.messages as m (m.id)}
        {#if !m.is_system && m.content}
          <div class={`msg ${m.role}`}>
            <div class="who small muted">{m.role === 'user' ? 'you' : m.role}{m.has_tool_use ? ' · tools' : ''} {when(m.timestamp)}</div>
            <pre class="text">{m.content}</pre>
          </div>
        {/if}
      {/each}
    </div>
  {:else}
    <h1>History</h1>
    <div class="filters">
      <input type="search" placeholder="Search all sessions" bind:value={q} oninput={onInput} />
      <select bind:value={agent} onchange={load}>
        <option value="">any agent</option>
        {#each ['claude', 'codex', 'pi', 'opencode', 'gemini'] as a}<option value={a}>{a}</option>{/each}
      </select>
      {#if machines.length > 1}
        <select bind:value={machine} onchange={load}>
          <option value="">any machine</option>
          {#each machines as m}<option value={m}>{m}</option>{/each}
        </select>
      {/if}
    </div>
    {#if error}<p class="notice err">{error}</p>{/if}
    {#if loading && rows.length === 0}<p class="notice muted">Loading…</p>{/if}
    {#if !loading && rows.length === 0 && !error}<p class="notice muted">No sessions match.</p>{/if}
    <ul>
      {#each rows as s (s.id)}
        <li>
          <button class="row" onclick={() => open(s)}>
            <div class="top">
              <span class="agent">{s.agent}</span>
              <span class="proj">{s.project}</span>
              <span class="mach muted small">{s.machine}</span>
              <span class="when muted small">{when(s.started_at)}</span>
            </div>
            <div class="first">{s.first_message || '(no prompt)'}</div>
            <div class="small muted">{s.message_count} messages · {shortCwd(s.cwd)}</div>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .filters { display: flex; gap: 8px; padding: 0 16px 10px; flex-wrap: wrap; }
  input[type='search'] { flex: 1 1 200px; padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  select { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  .notice { padding: 8px 16px; margin: 0; }
  .err { color: var(--signal); }
  ul { list-style: none; margin: 0; padding: 0; background: var(--surface); }
  li { border-bottom: 1px solid var(--hairline); }
  .row { display: block; width: 100%; text-align: left; border: none; border-radius: 0; background: none; padding: 10px 16px; }
  .row:hover { background: var(--page); border: none; }
  .top { display: flex; gap: 8px; align-items: baseline; }
  .agent { font-weight: 600; }
  .when { margin-left: auto; }
  .first { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dhead { position: sticky; top: 48px; background: var(--surface); border-bottom: 1px solid var(--hairline); padding: 10px 16px; display: grid; gap: 4px; }
  .title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .resume { padding: 6px 10px; background: var(--page); border-radius: var(--radius); user-select: all; }
  .msgs { padding: 8px 0 24px; }
  .msg { padding: 8px 16px; }
  .msg.user { background: var(--cobalt-soft); border-left: 3px solid var(--cobalt); margin: 8px 0; }
  .text { font-family: var(--sans); font-size: 15px; }
  @media (min-width: 960px) { .dhead { top: 0; } }
</style>
