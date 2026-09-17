<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import type { PATodo, PATodoPanel } from '../lib/types'
  import PaPanel from './PaPanel.svelte'

  let { onhandoff }: { onhandoff?: (t: PATodo) => void } = $props()

  let data = $state<PATodoPanel | null>(null)
  let loading = $state(true)
  let error = $state('')
  let busy = $state('')
  let msg = $state<{ ok: boolean; text: string } | null>(null)
  let open = $state('')
  let filter = $state('all')
  let more = $state<Record<string, boolean>>({})
  // new item
  let text = $state('')
  let date = $state(iso(1))
  let list = $state('PA')
  let project = $state('')
  let custom = $state<Record<string, string>>({})

  function iso(plusDays: number): string {
    const d = new Date()
    d.setDate(d.getDate() + plusDays)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  function nextMonday(): string {
    const dow = new Date().getDay()
    return iso(((8 - dow) % 7) || 7)
  }

  const groups: { id: PATodo['bucket']; label: string; first: number }[] = [
    { id: 'today', label: 'Today', first: 50 },
    { id: 'overdue', label: 'Overdue', first: 6 },
    { id: 'week', label: 'Next 7 days', first: 12 },
    { id: 'later', label: 'Later', first: 0 },
    { id: 'snoozed', label: 'Snoozed', first: 0 },
  ]
  const shown = $derived(
    (data?.items ?? []).filter((i) => filter === 'all' || (filter === 'projects' ? !!i.project : i.list === filter)),
  )
  const summary = $derived.by(() => {
    const c = data?.counts ?? {}
    const parts = []
    if (c.today) parts.push(`${c.today} today`)
    if (c.overdue) parts.push(`${c.overdue} overdue`)
    if (c.week) parts.push(`${c.week} this week`)
    return parts.join(' · ')
  })

  async function load(fresh = false) {
    loading = true
    try {
      const r = await api.paPanel<PATodoPanel>('todo', fresh)
      if (r.ok) {
        data = r
        error = ''
        if (!r.lists.includes(list)) list = r.lists[0] ?? 'PA'
      } else error = r.error ?? 'failed'
    } catch (e) {
      error = String(e)
    } finally {
      loading = false
    }
  }
  onMount(() => {
    load()
    const t = setInterval(() => document.visibilityState === 'visible' && load(), 120000)
    return () => clearInterval(t)
  })

  async function act(name: string, args: Record<string, unknown>, okText: string, id = '') {
    busy = id || name
    msg = null
    try {
      const r = await api.paAct(name, args)
      msg = r.ok ? { ok: true, text: okText + (r.warning ? ` (${r.warning})` : '') } : { ok: false, text: r.error ?? 'failed' }
      if (r.ok) await load(true)
      return r.ok
    } catch (e) {
      msg = { ok: false, text: String(e) }
      return false
    } finally {
      busy = ''
    }
  }
  async function add() {
    if (!text.trim()) return
    if (await act('todo_add', { text, date, list, project }, 'Added.')) text = ''
  }
  function when(t: PATodo): string {
    if (t.bucket === 'snoozed') return `until ${t.snoozed_until.slice(5)}`
    if (t.days === 0) return 'today'
    if (t.days === -1) return 'yesterday'
    if (t.days < 0) return `${-t.days} d late`
    if (t.days === 1) return 'tomorrow'
    if (t.days < 7) return new Date(t.date + 'T12:00').toLocaleDateString(undefined, { weekday: 'short' })
    return t.date.slice(5)
  }
</script>

<PaPanel id="todo" title="Todo" {summary} alert={(data?.counts?.today ?? 0) > 0} {loading} {error} onrefresh={() => load(true)}>
  <form class="add" onsubmit={(e) => { e.preventDefault(); add() }}>
    <input class="text" bind:value={text} placeholder="New todo: what, for whom, where the material is" maxlength="2000" />
    <div class="addrow">
      <input type="date" bind:value={date} aria-label="due" />
      <select bind:value={list} aria-label="list">
        {#each data?.lists ?? ['PA'] as l (l)}<option value={l}>{l}</option>{/each}
      </select>
      <select bind:value={project} aria-label="project">
        <option value="">no project</option>
        {#each data?.projects ?? [] as p (p.slug)}<option value={p.slug}>{p.name}</option>{/each}
      </select>
      <button class="primary" disabled={!text.trim() || busy === 'todo_add'}>{busy === 'todo_add' ? 'Adding…' : 'Add'}</button>
    </div>
  </form>
  {#if msg}<p class={`small ${msg.ok ? 'okmsg' : 'err'}`}>{msg.text}</p>{/if}

  {#if data}
    <div class="chips">
      {#each ['all', 'projects', ...data.lists] as f (f)}
        <button class="chip" class:on={filter === f} onclick={() => (filter = f)}>{f === 'all' ? 'All' : f === 'projects' ? 'Papers & projects' : f}</button>
      {/each}
      <span class="push"></span>
      <button class="ghost small" disabled={!!busy} onclick={() => act('todo_sync', {}, 'Synced with org and Google Tasks.')} title="Push to Google Tasks and org, pull completions back">{busy === 'todo_sync' ? 'Syncing…' : 'Sync'}</button>
    </div>

    {#each groups as g (g.id)}
      {@const all = shown.filter((i) => i.bucket === g.id)}
      {#if all.length}
        {@const rows = more[g.id] ? all : all.slice(0, g.first)}
        <h3 class={g.id}>
          {g.label} <span class="n">{all.length}</span>
          {#if all.length > g.first}
            <button class="ghost small" onclick={() => (more[g.id] = !more[g.id])}>{more[g.id] ? 'fewer' : g.first ? 'all' : 'show'}</button>
          {/if}
        </h3>
        <ul>
          {#each rows as t (t.list + t.id + t.title)}
            {@const isOpen = open === t.id}
            <li class:working={busy === t.id}>
              <div class="row">
                <button class="tick" disabled={!t.id || !!busy} onclick={() => act('todo_done', { id: t.id }, `Done: ${t.title}`, t.id)} aria-label={`Done: ${t.title}`} title="Done"></button>
                <button class="main" onclick={() => (open = isOpen ? '' : t.id)} aria-expanded={isOpen}>
                  <span class="title">{t.title}</span>
                  <span class="meta small muted">
                    <span class={`when ${t.bucket}`}>{when(t)}</span>
                    {#if t.list !== 'PA'} · {t.list}{/if}
                    {#if t.project_name} · <span class="proj">{t.project_name}</span>{/if}
                  </span>
                </button>
              </div>
              {#if isOpen}
                <div class="detail">
                  <p class="body">{t.body}</p>
                  <div class="acts">
                    <span class="small muted">Snooze</span>
                    <button disabled={!!busy} onclick={() => act('todo_snooze', { id: t.id, until: iso(1) }, 'Snoozed until tomorrow.', t.id)}>tomorrow</button>
                    <button disabled={!!busy} onclick={() => act('todo_snooze', { id: t.id, until: nextMonday() }, 'Snoozed until Monday.', t.id)}>Monday</button>
                    <button disabled={!!busy} onclick={() => act('todo_snooze', { id: t.id, until: iso(14) }, 'Snoozed for two weeks.', t.id)}>2 weeks</button>
                    {#if t.snoozed_until}<button disabled={!!busy} onclick={() => act('todo_unsnooze', { id: t.id }, 'Snooze removed.', t.id)}>wake</button>{/if}
                  </div>
                  <div class="acts">
                    <span class="small muted">Due</span>
                    <input type="date" value={custom[t.id] ?? t.date} oninput={(e) => (custom[t.id] = e.currentTarget.value)} aria-label="new due date" />
                    <button disabled={!!busy || !custom[t.id] || custom[t.id] === t.date} onclick={() => act('todo_due', { id: t.id, date: custom[t.id] }, `Due ${custom[t.id]}.`, t.id)}>Move</button>
                    <button disabled={!!busy || !custom[t.id]} onclick={() => act('todo_snooze', { id: t.id, until: custom[t.id] }, `Snoozed until ${custom[t.id]}.`, t.id)}>Snooze to date</button>
                    {#if onhandoff}<button onclick={() => onhandoff?.(t)}>Hand to an agent…</button>{/if}
                  </div>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    {/each}
    {#if shown.length === 0}<p class="small muted">Nothing open here.</p>{/if}
  {:else if loading}
    <p class="small muted">Asking the laptop…</p>
  {/if}
</PaPanel>

<style>
  .add { display: grid; gap: 6px; margin-bottom: 8px; }
  .text, .addrow input, .addrow select, .acts input { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); color: inherit; font: inherit; font-size: 14px; }
  .text { width: 100%; }
  .addrow { display: flex; flex-wrap: wrap; gap: 6px; }
  .addrow select { flex: 1; min-width: 90px; max-width: 240px; }
  .addrow button { font-size: 13px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin: 4px 0 2px; }
  .chip { font-size: 12px; padding: 2px 10px; border-radius: 12px; color: var(--muted); }
  .chip.on { color: var(--cobalt); border-color: var(--cobalt); background: var(--cobalt-soft); }
  .push { flex: 1; }
  .ghost { border-color: transparent; background: none; color: var(--muted); padding: 2px 8px; }
  .ghost:hover { color: var(--ink); border-color: var(--hairline); }
  h3 { margin: 12px 0 4px; font-size: 13px; font-weight: 600; display: flex; align-items: baseline; gap: 6px; }
  h3.overdue { color: var(--signal); }
  h3.today { color: var(--amber); }
  .n { font-weight: 400; color: var(--muted); font-family: var(--mono); font-size: 12px; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { border-bottom: 1px solid var(--hairline); }
  li.working { opacity: .5; }
  .row { display: flex; align-items: flex-start; gap: 4px; }
  .tick { flex: none; width: 20px; height: 20px; margin: 9px 6px 0 0; padding: 0; border-radius: 50%; border: 1.5px solid var(--muted); background: none; }
  .tick:hover:not(:disabled) { border-color: var(--moss); background: color-mix(in srgb, var(--moss) 25%, transparent); }
  .main { flex: 1; min-width: 0; display: grid; border: 0; background: none; border-radius: 0; padding: 7px 0; text-align: left; }
  .title { overflow-wrap: anywhere; }
  .when { font-family: var(--mono); font-size: 12px; }
  .when.overdue { color: var(--signal); }
  .when.today { color: var(--amber); }
  .proj { color: var(--cobalt); }
  .detail { padding: 0 0 10px 30px; }
  .body { margin: 0 0 8px; font-size: 14px; max-width: 72ch; overflow-wrap: anywhere; }
  .acts { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
  .acts button { font-size: 13px; padding: 3px 10px; }
  .err { color: var(--signal); }
  .okmsg { color: var(--moss); }
  @media (max-width: 600px) { .detail { padding-left: 0; } }
</style>
