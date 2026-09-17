<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { Fleet, fleet } from '../lib/store.svelte'
  import { ago } from '../lib/format'
  import type { PAItem, PAState } from '../lib/types'

  let { wide = false }: { wide?: boolean } = $props()
  let pa = $state<PAState | null>(null)
  let loading = $state(true)
  let starting = $state(false)
  let runMsg = $state('')
  let focus = $state('')
  let showFocus = $state(false)
  let showAll = $state(false)
  let openItem = $state('')
  // per item: edited texts, chosen targets, progress
  let body = $state<Record<string, string>>({})
  let prompt = $state<Record<string, string>>({})
  let where = $state<Record<string, string>>({})
  let agent = $state<Record<string, string>>({})
  let tell = $state<Record<string, string>>({})
  let confirmSend = $state('')
  let busy = $state<Record<string, string>>({})
  let note = $state<Record<string, { ok: boolean; text: string }>>({})

  const target = $derived(wide ? '_blank' : undefined)
  const b = $derived(pa?.briefing ?? null)
  const running = $derived(pa?.session?.status === 'busy')
  const items = $derived((b?.items ?? []).filter((i) => showAll || i.status === 'open'))
  const hidden = $derived((b?.items ?? []).length - items.length)
  const generated = $derived(b?.generated_at ? Date.parse(b.generated_at) : (b?.file_mtime ?? 0))
  const rank: Record<string, number> = { now: 0, today: 1, week: 2, fyi: 3 }
  const sorted = $derived([...items].sort((x, y) => (rank[x.urgency] ?? 9) - (rank[y.urgency] ?? 9)))

  /** Places a task can run: the listed workspaces, plus the folder the assistant proposed. */
  function places(item: PAItem) {
    const out = (pa?.workspaces ?? []).map((w) => ({ id: `${w.machine ?? ''}|${w.path}`, label: `${w.name ?? w.path} · ${w.machine ?? ''}`, machine: w.machine ?? '', path: w.path }))
    const t = item.task
    if (t?.path && !out.some((o) => o.path === t.path && o.machine === (t.machine ?? ''))) {
      out.unshift({ id: `${t.machine ?? ''}|${t.path}`, label: `${t.path} · ${t.machine ?? ''} (proposed)`, machine: t.machine ?? '', path: t.path })
    }
    return out
  }
  function defaultPlace(item: PAItem): string {
    const t = item.task
    const list = places(item)
    const byName = t?.workspace ? (pa?.workspaces ?? []).find((w) => w.name === t.workspace) : undefined
    if (byName) return `${byName.machine ?? ''}|${byName.path}`
    if (t?.path) return `${t.machine ?? ''}|${t.path}`
    return list[0]?.id ?? ''
  }
  const agents = $derived(
    fleet.sessionList.filter((s) => ['busy', 'idle', 'waiting'].includes(s.status) && Fleet.canSend(s) && !(s.extra as any)?.parent && !Fleet.isStale(s)),
  )

  async function load() {
    try {
      pa = await api.pa()
    } catch (e) {
      pa = { ok: false, error: String(e), briefing: null, session: null, workspaces: [], roots: [] }
    } finally {
      loading = false
    }
  }
  onMount(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  })
  // the assistant's session going idle means a new briefing file is there
  let lastStatus = ''
  $effect(() => {
    const st = pa?.session ? (fleet.sessions[pa.session.key]?.status ?? pa.session.status) : ''
    if (lastStatus === 'busy' && st !== 'busy') load()
    lastStatus = st
  })

  async function run() {
    starting = true
    runMsg = ''
    try {
      const r = await api.paRun(focus)
      runMsg = r.ok ? (r.already_running ? 'Already working on one.' : 'Started. It takes a few minutes; the result appears here.') : (r.error ?? 'failed')
      if (r.ok) {
        focus = ''
        showFocus = false
      }
      setTimeout(load, 8000)
    } catch (e) {
      runMsg = String(e)
    } finally {
      starting = false
    }
  }

  async function act(item: PAItem, what: string, fn: () => Promise<{ ok: boolean; error?: string }>, okText: string) {
    busy[item.id] = what
    try {
      const r = await fn()
      note[item.id] = r.ok ? { ok: true, text: okText } : { ok: false, text: r.error ?? 'failed' }
      if (r.ok) await load()
    } catch (e) {
      note[item.id] = { ok: false, text: String(e) }
    } finally {
      busy[item.id] = ''
      confirmSend = ''
    }
  }
  const mark = (item: PAItem, status: string, text: string, n = '') => act(item, status, () => api.paItem(item.id, 'mark', { status, note: n }), text)

  function sendMail(item: PAItem) {
    const edited = body[item.id]
    const changed = edited !== undefined && edited.trim() !== (item.draft?.body ?? '').trim()
    return act(item, 'send', () => api.paItem(item.id, 'send_email', { body: changed ? edited : null }), `Sent through Emacs to ${item.draft?.to ?? ''}.`)
  }
  function spawn(item: PAItem) {
    const place = places(item).find((p) => p.id === (where[item.id] ?? defaultPlace(item)))
    if (!place) return
    const text = prompt[item.id] ?? item.task?.prompt ?? ''
    const name = (item.task?.title ?? 'task').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)
    return act(
      item,
      'spawn',
      async () => {
        const r = await api.startSession(place.machine, { cwd: place.path, prompt: text, name })
        if (r.ok) await api.paItem(item.id, 'mark', { status: 'delegated', note: `new agent in ${place.path} on ${place.machine}` })
        return { ok: r.ok, error: r.error ?? r.output }
      },
      `Agent started in ${place.path}.`,
    )
  }
  function handOver(item: PAItem) {
    const key = agent[item.id]
    if (!key) return
    const text = prompt[item.id] ?? item.task?.prompt ?? ''
    return act(
      item,
      'hand',
      async () => {
        const r = await api.prompt(key, text)
        if (r.ok) await api.paItem(item.id, 'mark', { status: 'delegated', note: `sent to ${fleet.sessions[key]?.name ?? key}` })
        return r
      },
      `Sent to ${fleet.sessions[key]?.name ?? 'the agent'}.`,
    )
  }
  function tellPA(item: PAItem, text: string, okText: string) {
    const key = pa?.session?.key
    if (!key || !text.trim()) return
    const ref = `About briefing item ${item.id} (${item.subject ?? item.ask ?? ''}): `
    return act(item, 'tell', () => api.prompt(key, ref + text.trim()), okText)
  }
  const channelSend = (item: PAItem) =>
    tellPA(
      item,
      `Robert authorizes sending exactly this reply on ${item.draft?.kind} to ${item.from ?? 'the sender'}, now, and nothing else:\n\n${body[item.id] ?? item.draft?.body ?? ''}\n\nThen mark the item handled in the overlay file.`,
      'The assistant was told to send it.',
    )
</script>

<section class="pa">
  <div class="head">
    <h2>Personal briefing</h2>
    {#if running}
      <span class="small live">assistant is working…</span>
    {:else if generated}
      <span class="small muted">{ago(generated)} ago{pa?.machine ? ` · ${pa.machine}` : ''}</span>
    {/if}
    {#if pa?.session}<a class="small" href={`#/session/${encodeURIComponent(pa.session.key)}`} {target}>session{wide ? ' ↗' : ''}</a>{/if}
    <span class="push"></span>
    <button class="ghost" onclick={() => (showFocus = !showFocus)} title="Add a note for this run">note</button>
    <button disabled={starting || running || !pa?.ok} onclick={run}>{b ? 'New briefing' : 'Get briefing'}</button>
  </div>
  {#if showFocus}
    <input class="focus" bind:value={focus} placeholder="Optional: what to look at first, e.g. 'only mail since Monday'" />
  {/if}
  {#if runMsg}<p class="small muted">{runMsg}</p>{/if}

  {#if loading}
    <p class="small muted">Asking the laptop…</p>
  {:else if pa && !pa.ok}
    <p class="small err">{pa.error}</p>
  {:else if !b}
    <p class="small muted">The assistant in ~/pa reads mail, Mattermost, WhatsApp, calendar and deadlines, drafts the replies it can, and turns the rest into tasks you can hand to other agents. Nothing is sent without you.</p>
  {:else}
    {#if b.summary}<p class="summary">{b.summary}</p>{/if}

    {#if (b.schedule?.length ?? 0) + (b.deadlines?.length ?? 0) > 0}
      <details class="cal">
        <summary>{b.schedule?.length ?? 0} on the calendar · {b.deadlines?.length ?? 0} deadlines</summary>
        <div class="cols">
          <ul>
            {#each b.schedule ?? [] as e}
              <li><span class="when">{e.when.replace('T', ' ').slice(0, 16)}</span> {e.what}{#if e.note}<span class="warnnote"> {e.note}</span>{/if}</li>
            {/each}
          </ul>
          <ul>
            {#each b.deadlines ?? [] as d}
              <li><span class="when">{d.date}</span> {d.what}</li>
            {/each}
          </ul>
        </div>
      </details>
    {/if}

    <ul class="items">
      {#each sorted as item (item.id)}
        {@const isOpen = openItem === item.id}
        {@const mail = item.draft?.kind === 'email_reply' || item.draft?.kind === 'email_new'}
        <li class={`item ${item.urgency}`} class:closed={item.status !== 'open'}>
          <button class="row" onclick={() => (openItem = isOpen ? '' : item.id)} aria-expanded={isOpen}>
            <span class={`urg ${item.urgency}`}>{item.urgency}</span>
            <span class="main">
              <span class="subj">{item.subject || item.task?.title || item.ask}</span>
              <span class="meta small muted">{item.channel}{item.from ? ` · ${item.from.replace(/<.*>/, '').trim()}` : ''}{item.status !== 'open' ? ` · ${item.status}${item.status_note ? `: ${item.status_note}` : ''}` : ''}</span>
            </span>
            <span class="tags">
              {#if item.draft}<span class="tag">{mail ? 'draft' : 'reply'}</span>{/if}
              {#if item.task}<span class="tag t">task</span>{/if}
              {#if item.needs_decision}<span class="tag d">decide</span>{/if}
            </span>
          </button>

          {#if isOpen}
            <div class="body">
              {#if item.ask}<p class="ask">{item.ask}</p>{/if}
              {#if item.needs_decision}<p class="decide"><b>Your call:</b> {item.needs_decision}</p>{/if}
              {#if item.link}<p class="small"><a href={item.link} target="_blank" rel="noopener">Open original ↗</a></p>{/if}

              {#if item.draft}
                <div class="block">
                  <div class="bt small muted">{mail ? `Reply to ${item.draft.to ?? item.from ?? ''}${item.draft.cc ? `, cc ${item.draft.cc}` : ''} · ${item.draft.subject ?? ''}` : `Proposed ${item.draft.kind} reply`}</div>
                  <textarea rows={Math.min(14, Math.max(4, (body[item.id] ?? item.draft.body).split('\n').length + 1))} value={body[item.id] ?? item.draft.body} oninput={(e) => (body[item.id] = e.currentTarget.value)} disabled={item.status === 'sent'}></textarea>
                  {#if item.status !== 'sent'}
                    <div class="acts">
                      {#if mail}
                        {#if confirmSend === item.id}
                          <span class="small">Send to {item.draft.to ?? item.from}?</span>
                          <button class="primary" disabled={!!busy[item.id]} onclick={() => sendMail(item)}>{busy[item.id] === 'send' ? 'Sending…' : 'Yes, send'}</button>
                          <button onclick={() => (confirmSend = '')}>No</button>
                        {:else}
                          <button class="primary" onclick={() => (confirmSend = item.id)}>Send via Emacs</button>
                          <button disabled={!!busy[item.id]} onclick={() => act(item, 'discard', () => api.paItem(item.id, 'discard'), 'Draft discarded.')}>Discard draft</button>
                        {/if}
                      {:else}
                        <button class="primary" disabled={!pa?.session || !!busy[item.id]} onclick={() => channelSend(item)} title={pa?.session ? '' : 'The assistant session is not running'}>Have the assistant send it</button>
                        <button onclick={() => navigator.clipboard?.writeText(body[item.id] ?? item.draft?.body ?? '')}>Copy</button>
                      {/if}
                    </div>
                  {/if}
                </div>
              {/if}

              {#if item.task}
                <div class="block">
                  <div class="bt small muted">Task{item.task.deadline ? ` · due ${item.task.deadline}` : ''}: {item.task.title}</div>
                  <textarea rows={Math.min(12, Math.max(4, (prompt[item.id] ?? item.task.prompt).split('\n').length + 1))} value={prompt[item.id] ?? item.task.prompt} oninput={(e) => (prompt[item.id] = e.currentTarget.value)}></textarea>
                  <div class="acts">
                    <select value={where[item.id] ?? defaultPlace(item)} onchange={(e) => (where[item.id] = e.currentTarget.value)} aria-label="where the new agent works">
                      {#each places(item) as p (p.id)}<option value={p.id}>{p.label}</option>{/each}
                    </select>
                    <button class="primary" disabled={!!busy[item.id] || !places(item).length} onclick={() => spawn(item)}>{busy[item.id] === 'spawn' ? 'Starting…' : 'Start new agent'}</button>
                  </div>
                  <div class="acts">
                    <select value={agent[item.id] ?? ''} onchange={(e) => (agent[item.id] = e.currentTarget.value)} aria-label="running agent">
                      <option value="">a running agent…</option>
                      {#each agents as s (s.key)}<option value={s.key}>{s.name || s.session_id.slice(0, 8)} · {s.machine} · {s.status}</option>{/each}
                    </select>
                    <button disabled={!agent[item.id] || !!busy[item.id]} onclick={() => handOver(item)}>{busy[item.id] === 'hand' ? 'Sending…' : 'Hand over'}</button>
                  </div>
                </div>
              {/if}

              <div class="acts foot">
                <input class="tell" placeholder={pa?.session ? 'Tell the assistant what to do with this…' : 'Assistant session not running'} disabled={!pa?.session} value={tell[item.id] ?? ''} oninput={(e) => (tell[item.id] = e.currentTarget.value)}
                  onkeydown={(e) => { if (e.key === 'Enter') { tellPA(item, tell[item.id] ?? '', 'Told the assistant.'); tell[item.id] = '' } }} />
                {#if item.status === 'open'}
                  <button disabled={!!busy[item.id]} onclick={() => mark(item, 'done', 'Marked done.')}>Done</button>
                  <button class="ghost" disabled={!!busy[item.id]} onclick={() => mark(item, 'ignored', 'Ignored.')}>Ignore</button>
                {:else}
                  <button class="ghost" onclick={() => mark(item, 'open', 'Reopened.')}>Reopen</button>
                {/if}
              </div>
              {#if note[item.id]}<p class={`small ${note[item.id].ok ? 'okmsg' : 'err'}`}>{note[item.id].text}</p>{/if}
            </div>
          {/if}
        </li>
      {/each}
    </ul>
    {#if sorted.length === 0}<p class="small muted">Nothing open.</p>{/if}
    {#if hidden > 0 || showAll}
      <button class="ghost small" onclick={() => (showAll = !showAll)}>{showAll ? 'Hide handled items' : `Show ${hidden} handled`}</button>
    {/if}
  {/if}
</section>

<style>
  .pa { padding: 0 16px; }
  .head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  h2 { margin: 0; padding: 18px 0 6px; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  .push { flex: 1; }
  .head button { font-size: 13px; padding: 3px 10px; }
  .ghost { border-color: transparent; background: none; color: var(--muted); }
  .ghost:hover { color: var(--ink); border-color: var(--hairline); }
  .live { color: var(--cobalt); }
  .focus { width: 100%; margin: 4px 0 6px; padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  .summary { margin: 4px 0 10px; max-width: 68ch; }
  .err { color: var(--signal); }
  .okmsg { color: var(--moss); }
  .cal { margin: 0 0 10px; font-size: 13px; }
  .cal summary { cursor: pointer; color: var(--muted); }
  .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 24px; }
  .cal ul { list-style: none; margin: 6px 0 0; padding: 0; }
  .cal li { padding: 3px 0; border-bottom: 1px solid var(--hairline); }
  .when { font-family: var(--mono); font-size: 12px; color: var(--muted); margin-right: 6px; }
  .warnnote { color: var(--amber); }
  .items { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .item { background: var(--surface); border: 1px solid var(--hairline); border-left: 3px solid var(--hairline); border-radius: var(--radius); }
  .item.now { border-left-color: var(--signal); }
  .item.today { border-left-color: var(--amber); }
  .item.week { border-left-color: var(--cobalt); }
  .item.closed { opacity: .6; }
  .row { display: flex; gap: 10px; align-items: flex-start; width: 100%; border: 0; background: none; border-radius: 0; padding: 9px 12px; text-align: left; }
  .urg { flex: none; width: 44px; font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); padding-top: 2px; }
  .urg.now { color: var(--signal); }
  .urg.today { color: var(--amber); }
  .main { flex: 1; min-width: 0; display: grid; }
  .subj { font-weight: 500; overflow-wrap: anywhere; }
  .meta { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tags { display: flex; gap: 4px; flex: none; }
  .tag { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 8px; color: var(--cobalt); background: var(--cobalt-soft); }
  .tag.t { color: var(--moss); background: color-mix(in srgb, var(--moss) 14%, transparent); }
  .tag.d { color: var(--signal); background: var(--signal-soft); }
  .body { padding: 0 12px 12px 66px; }
  @media (max-width: 600px) { .body { padding-left: 12px; } }
  .ask { margin: 0 0 6px; }
  .decide { margin: 0 0 8px; padding: 6px 10px; background: var(--signal-soft); border-radius: var(--radius); font-size: 14px; }
  .block { margin-top: 10px; }
  .bt { margin-bottom: 4px; overflow-wrap: anywhere; }
  textarea { width: 100%; padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); font-family: var(--mono); font-size: 12.5px; line-height: 1.45; resize: vertical; }
  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
  .acts button, .acts select { font-size: 13px; padding: 4px 10px; }
  .acts select { flex: 1; min-width: 160px; max-width: 420px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); color: inherit; font: inherit; font-size: 13px; }
  .foot { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--hairline); }
  .tell { flex: 1; min-width: 180px; padding: 5px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font-size: 13px; }
</style>
