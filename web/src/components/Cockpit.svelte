<script lang="ts">
  import { onMount } from 'svelte'
  import { Fleet, fleet } from '../lib/store.svelte'
  import { ago } from '../lib/format'
  import { api } from '../lib/api'
  import DecisionCard from './DecisionCard.svelte'
  import PersonalBriefing from './PersonalBriefing.svelte'
  import type { Finding, Session, Suggestion } from '../lib/types'

  const c = $derived(fleet.cockpit)
  const act = $derived(c?.findings.filter((f) => f.severity === 'act') ?? [])
  const warn = $derived(c?.findings.filter((f) => f.severity === 'warn') ?? [])
  const info = $derived(c?.findings.filter((f) => f.severity === 'info') ?? [])
  const b = $derived(c?.briefing ?? null)
  const doing = $derived(
    Object.entries(b?.sessions ?? {})
      .map(([key, text]) => ({ key, text, s: fleet.sessions[key] }))
      .filter((x) => x.s),
  )
  let cleaning = $state('')
  let note = $state<Record<string, string>>({})
  let copied = $state(-1)
  /** Per suggestion: what happened when it was carried out from here. */
  let done = $state<Record<number, { state: 'working' | 'ok' | 'failed'; text: string }>>({})
  let confirmStop = $state(-1)
  let silencing = $state('') // id whose duration choice is open
  let showSilenced = $state(false)
  async function silence(id: string, title: string, hours: number | null) {
    silencing = ''
    await api.silence(id, title, hours)
    await fleet.loadCockpit(false)
  }
  async function unsilence(id: string) {
    await api.unsilence(id)
    await fleet.loadCockpit(false)
  }

  // On a wide screen the cockpit is one pane of a split view: opening a session in the same
  // tab would replace it, so links go to a new tab. On the phone a new tab leaves the app.
  let wide = $state(false)
  onMount(() => {
    const mq = window.matchMedia('(min-width: 960px)')
    const set = () => (wide = mq.matches)
    set()
    mq.addEventListener('change', set)
    return () => mq.removeEventListener('change', set)
  })
  const target = $derived(wide ? '_blank' : undefined)
  function go(href: string) {
    if (wide) window.open(location.pathname + location.search + href, '_blank', 'noopener')
    else location.hash = href
  }
  const sessionHref = (key: string) => `#/session/${encodeURIComponent(key)}`
  const decisionOf = (f: Finding) => (f.kind === 'decision' ? fleet.decisions[f.id.slice('decision:'.length)] : undefined)

  function stopAction(s: Session | undefined): string {
    if (!s || !['busy', 'idle', 'waiting'].includes(s.status)) return ''
    if (s.harness === 'claude' && s.kind === 'background') return 'stop'
    return s.pid ? 'terminate' : ''
  }

  async function send(s: Suggestion, i: number) {
    done[i] = { state: 'working', text: 'Sending…' }
    try {
      const r = await api.prompt(s.session_key, s.prompt)
      done[i] = r.ok ? { state: 'ok', text: `Sent to ${sessionName(s.session_key)}.` } : { state: 'failed', text: `Not delivered: ${r.error ?? 'failed'}` }
    } catch (e) {
      done[i] = { state: 'failed', text: String(e) }
    }
  }
  async function stop(s: Suggestion, i: number) {
    const action = stopAction(fleet.sessions[s.session_key])
    confirmStop = -1
    if (!action) return
    done[i] = { state: 'working', text: 'Stopping…' }
    try {
      const r = await api.action(s.session_key, action)
      done[i] = r.ok ? { state: 'ok', text: `${sessionName(s.session_key)} stopped.` } : { state: 'failed', text: r.error ?? r.output ?? 'failed' }
    } catch (e) {
      done[i] = { state: 'failed', text: String(e) }
    }
  }

  onMount(() => {
    fleet.loadCockpit(true)
    const t = setInterval(() => fleet.loadCockpit(false), 20000)
    return () => clearInterval(t)
  })

  const providerName: Record<string, string> = { anthropic: 'Claude', openai: 'Codex' }
  const kindLabel: Record<string, string> = {
    answer: 'answer',
    switch_harness: 'switch harness',
    compact: 'compact',
    handoff: 'fresh session',
    fan_out: 'fan out',
    new_session: 'new session',
    stop: 'stop',
    other: 'idea',
  }

  async function run(f: Finding) {
    const a = f.action
    if (!a) return
    if (a.type === 'send_prompt') {
      cleaning = f.id
      try {
        const r = await api.prompt(a.keys[0], a.prompt)
        note[f.id] = r.ok ? `${a.prompt} typed into the session.` : `Not delivered: ${r.error ?? 'failed'}`
      } catch (e) {
        note[f.id] = String(e)
      } finally {
        cleaning = ''
      }
      return
    }
    if (a.type !== 'cleanup') {
      go(a.href)
      return
    }
    cleaning = f.id
    try {
      const r = await fleet.cleanup(a.machine, a.keys)
      const failed = r.results.filter((x) => !x.ok).length
      note[f.id] = `Removed ${r.removed}${failed ? `, ${failed} could not be removed` : ''}.`
      await fleet.loadCockpit(false)
    } catch (e) {
      note[f.id] = String(e)
    } finally {
      cleaning = ''
    }
  }

  function useDraft(s: Suggestion) {
    // a new tab has its own store, so the draft travels in the URL there
    if (wide) window.open(`${location.pathname}${location.search}${sessionHref(s.session_key)}?draft=${encodeURIComponent(s.prompt)}`, '_blank', 'noopener')
    else {
      fleet.drafts[s.session_key] = s.prompt
      location.hash = sessionHref(s.session_key)
    }
  }
  async function copy(s: Suggestion, i: number) {
    try {
      await navigator.clipboard.writeText(s.prompt)
      copied = i
      setTimeout(() => (copied = -1), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }
  const sessionName = (key: string) => fleet.sessions[key]?.name || key.split(':').pop()?.slice(0, 8) || ''
</script>

{#snippet quiet(id: string, title: string)}
  {#if silencing === id}
    <span class="qs">
      <button onclick={() => silence(id, title, 4)}>4 h</button>
      <button onclick={() => silence(id, title, 24)}>1 day</button>
      <button onclick={() => silence(id, title, 168)}>1 week</button>
      <button onclick={() => silence(id, title, null)}>for good</button>
      <button class="x" aria-label="cancel" onclick={() => (silencing = '')}>×</button>
    </span>
  {:else}
    <button class="q" title="Stop showing this" onclick={() => (silencing = id)}>Silence</button>
  {/if}
{/snippet}

{#snippet findingList(items: Finding[])}
  <ul class="findings">
    {#each items as f (f.id)}
      {@const d = decisionOf(f)}
      {#if d && d.status === 'pending'}
        <li class="act card"><DecisionCard {d} compact /></li>
      {:else}
      <li class={f.severity}>
        <div class="ftext">
          <div class="ftitle">{f.title}</div>
          {#if f.detail}<div class="fdetail">{f.detail}</div>{/if}
          {#if note[f.id]}<div class="fdetail">{note[f.id]}</div>{/if}
        </div>
        {#if f.action}
          <button class:primary={f.severity === 'act'} disabled={cleaning === f.id} onclick={() => run(f)}>
            {cleaning === f.id ? 'Working…' : f.action.label}{wide && !['cleanup', 'send_prompt'].includes(f.action.type) ? ' ↗' : ''}
          </button>
        {/if}
        {@render quiet(f.id, f.title)}
      </li>
      {/if}
    {/each}
  </ul>
{/snippet}

<div class="cockpit">
  {#if !c}
    <p class="muted pad">Reading the fleet…</p>
  {:else}
    <header class:hot={act.length > 0} class:calm={act.length === 0 && warn.length === 0}>
      <h1>{c.headline.split('. ')[0]}.</h1>
      <div class="stats">
        <span><b class="busy">{c.stats.busy}</b> working</span>
        {#if c.stats.subagents}<span><b class="dim">{c.stats.subagents}</b> sub-agent{c.stats.subagents === 1 ? '' : 's'}</span>{/if}
        <span><b class="wait">{c.stats.waiting}</b> waiting</span>
        <span><b>{c.stats.idle}</b> idle</span>
        {#if c.stats.stale}<span><b class="dim">{c.stats.stale}</b> stale</span>{/if}
        <span><b class:wait={c.stats.machines_online < c.stats.machines}>{c.stats.machines_online}/{c.stats.machines}</b> machines</span>
      </div>
      {#if c.headroom.length}
        <div class="headroom">
          {#each c.headroom as h (h.provider + h.account)}
            <a href="#/limits" {target} class="room" title={`Most-used window: ${h.window}`}>
              <span class="rl">{h.label}</span>
              <span class="bar"><span class="fill" class:amber={h.used_pct >= 80} class:red={h.used_pct >= 95} style={`width:${Math.min(100, h.used_pct)}%`}></span></span>
              <span class="rp">{h.used_pct.toFixed(0)}%</span>
            </a>
          {/each}
        </div>
      {/if}
    </header>

    {#if act.length}
      <h2 class="act">Needs you</h2>
      {@render findingList(act)}
    {/if}
    {#if warn.length}
      <h2 class="warn">Watch</h2>
      {@render findingList(warn)}
    {/if}

    <section class="brief">
      <div class="bhead">
        <h2>Briefing</h2>
        {#if c.generating}
          <span class="small muted">thinking…</span>
        {:else if b}
          <span class="small muted">{ago(b.generated_at)} ago{c.outdated ? ', fleet changed since' : ''}</span>
        {/if}
        <button class="refresh" disabled={c.generating} onclick={() => fleet.refreshBriefing()}>{b ? 'Refresh' : 'Generate'}</button>
      </div>
      {#if c.error && !c.generating}
        <p class="small err">No briefing: {c.error}</p>
      {/if}
      {#if b}
        <p class="summary" class:stale={c.outdated}>{b.summary}</p>
        {#if b.suggestions.length}
          <ol class="sug">
            {#each b.suggestions as s, i (i)}
              {@const target_s = s.session_key ? fleet.sessions[s.session_key] : undefined}
              <li>
                <div class="stitle"><span class="chip">{kindLabel[s.kind] ?? s.kind}</span>{s.title}</div>
                {#if s.why}<div class="fdetail">{s.why}</div>{/if}
                {#if s.prompt}<pre class="prompt">{s.prompt}</pre>{/if}
                <div class="sact">
                  {#if done[i]}
                    <span class={`result ${done[i].state}`}>{done[i].text}</span>
                  {/if}
                  {#if target_s && done[i]?.state !== 'ok' && done[i]?.state !== 'working'}
                    {#if s.prompt && Fleet.canDeliver(target_s, s.prompt)}
                      <button class="primary" onclick={() => send(s, i)}>Send to {sessionName(s.session_key)}</button>
                      <button onclick={() => useDraft(s)}>Edit first{wide ? ' ↗' : ''}</button>
                    {/if}
                    {#if s.kind === 'stop' && stopAction(target_s)}
                      {#if confirmStop === i}
                        <span class="small">End {sessionName(s.session_key)}?</span>
                        <button class="danger" onclick={() => stop(s, i)}>Yes, end it</button>
                        <button onclick={() => (confirmStop = -1)}>No</button>
                      {:else}
                        <button class="danger" onclick={() => (confirmStop = i)}>{stopAction(target_s) === 'stop' ? 'Stop it' : 'End session'}</button>
                      {/if}
                    {/if}
                  {/if}
                  {#if s.prompt && !(target_s && Fleet.canDeliver(target_s, s.prompt))}
                    <span class="small muted">{s.prompt.trim().startsWith('/') ? 'Type this in its terminal (not in tmux, so it cannot be sent).' : 'This session cannot receive prompts from here.'}</span>
                  {/if}
                  {#if s.prompt}<button onclick={() => copy(s, i)}>{copied === i ? 'Copied' : 'Copy'}</button>{/if}
                  {#if target_s}<a href={sessionHref(s.session_key)} {target}>Open{wide ? ' ↗' : ''}</a>{/if}
                  {#if s.kind === 'new_session'}<a href="#/new" {target}>New session{wide ? ' ↗' : ''}</a>{/if}
                  {#if s.kind === 'switch_harness'}<a href="#/limits" {target}>Limits{wide ? ' ↗' : ''}</a>{/if}
                  <span class="push"></span>
                  {@render quiet(s.id, s.title)}
                </div>
              </li>
            {/each}
          </ol>
        {/if}
        {#if doing.length}
          <h3>What each agent is doing</h3>
          <ul class="doing">
            {#each doing as x (x.key)}
              <li>
                <a href={sessionHref(x.key)} {target}>
                  <span class={`dot ${x.s.status}`}></span>
                  <span class="dn">{x.s.name || x.s.session_id.slice(0, 8)}</span>
                  <span class="dm muted">{x.s.machine} · {x.s.harness}{typeof x.s.extra?.context_pct === 'number' ? ` · ctx ${x.s.extra.context_pct.toFixed(0)}%` : ''}</span>
                  <span class="dt">{x.text}</span>
                </a>
              </li>
            {/each}
          </ul>
        {/if}
        <p class="small muted meta">{b.model} via {b.via}{typeof b.cost_usd === 'number' ? `, $${b.cost_usd.toFixed(3)}` : ''}. Nothing is sent until you press Send.</p>
      {:else if !c.generating && !c.error}
        <p class="small muted">A model reads the roster, limits and last outputs and proposes what to do next. Nothing is sent to any session.</p>
      {/if}
    </section>

    <PersonalBriefing {wide} />

    {#if info.length}
      <h2>Notes</h2>
      {@render findingList(info)}
    {/if}
    {#if c.silenced.length}
      <div class="silenced">
        <button class="q" onclick={() => (showSilenced = !showSilenced)}>{showSilenced ? '▾' : '▸'} {c.silenced.length} silenced</button>
        {#if showSilenced}
          <ul>
            {#each c.silenced as x (x.id)}
              <li>
                <span class="st">{x.title || x.id}</span>
                <span class="muted small">{x.until ? `until ${new Date(x.until).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` : 'for good'}</span>
                <button onclick={() => unsilence(x.id)}>Restore</button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
    {#if !c.findings.length}
      <p class="muted pad">No findings. Every session is either working or has nothing to ask.</p>
    {/if}
  {/if}
</div>

<style>
  .cockpit { padding-bottom: 32px; }
  .pad { padding: 16px; margin: 0; }
  header { padding: 18px 16px 16px; border-bottom: 1px solid var(--hairline); background: var(--surface); border-left: 4px solid var(--amber); }
  header.hot { border-left-color: var(--signal); background: var(--signal-soft); }
  header.calm { border-left-color: var(--moss); }
  h1 { margin: 0 0 10px; font-size: 22px; line-height: 1.2; font-weight: 600; letter-spacing: -0.01em; }
  .stats { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 13px; color: var(--muted); }
  .stats b { font-size: 17px; font-weight: 600; color: var(--ink); margin-right: 3px; font-variant-numeric: tabular-nums; }
  .stats b.busy { color: var(--cobalt); }
  .stats b.wait { color: var(--signal); }
  .stats b.dim { color: var(--muted); }
  .headroom { margin-top: 12px; display: grid; gap: 6px; max-width: 520px; }
  .room { display: grid; grid-template-columns: minmax(56px, auto) 1fr 40px; gap: 8px; align-items: center; font-size: 13px; color: var(--muted); text-decoration: none; }
  .bar { height: 6px; border-radius: 3px; background: var(--hairline); overflow: hidden; }
  .fill { display: block; height: 100%; background: var(--cobalt); }
  .fill.amber { background: var(--amber); }
  .fill.red { background: var(--signal); }
  .rp { text-align: right; font-variant-numeric: tabular-nums; }
  h2 { margin: 0; padding: 18px 16px 6px; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  h2.act { color: var(--signal); }
  h2.warn { color: var(--amber); }
  h3 { margin: 18px 0 6px; font-size: 13px; font-weight: 600; color: var(--muted); }
  .findings { list-style: none; margin: 0; padding: 0; background: var(--surface); border-top: 1px solid var(--hairline); }
  .findings li { display: flex; gap: 12px; align-items: flex-start; padding: 11px 16px 12px 13px; border-bottom: 1px solid var(--hairline); border-left: 3px solid var(--hairline); }
  .findings li.act { border-left-color: var(--signal); }
  .findings li.warn { border-left-color: var(--amber); }
  .ftext { flex: 1; min-width: 0; }
  .ftitle, .stitle { font-weight: 500; overflow-wrap: anywhere; }
  .fdetail { color: var(--muted); font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
  .findings button { flex: none; font-size: 13px; padding: 4px 10px; white-space: nowrap; }
  button.q { border-color: transparent; background: none; color: var(--muted); font-size: 12.5px; padding: 4px 6px; }
  button.q:hover { color: var(--ink); border-color: var(--hairline); }
  .qs { display: inline-flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .qs button { font-size: 12px; padding: 2px 8px; }
  .qs .x { border-color: transparent; background: none; color: var(--muted); }
  .push { flex: 1; }
  .findings li { flex-wrap: wrap; }
  .silenced { padding: 14px 16px 0; }
  .silenced ul { list-style: none; margin: 6px 0 0; padding: 0; }
  .silenced li { display: flex; gap: 10px; align-items: baseline; padding: 5px 0; border-bottom: 1px solid var(--hairline); font-size: 13px; }
  .silenced .st { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
  .silenced li button { font-size: 12px; padding: 2px 8px; }
  .brief { padding: 0 16px; }
  .bhead { display: flex; align-items: baseline; gap: 10px; }
  .bhead h2 { padding-left: 0; }
  .refresh { margin-left: auto; font-size: 13px; padding: 3px 10px; }
  .summary { margin: 4px 0 0; max-width: 68ch; }
  .summary.stale { color: var(--muted); }
  .err { color: var(--signal); margin: 4px 0; }
  .sug { margin: 12px 0 0; padding: 0; list-style: none; counter-reset: s; display: grid; gap: 10px; }
  .sug li { counter-increment: s; background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); padding: 10px 12px; }
  .chip { display: inline-block; margin-right: 8px; padding: 0 7px; border-radius: 9px; font-size: 11px; font-weight: 600; letter-spacing: .03em; text-transform: uppercase; color: var(--cobalt); background: var(--cobalt-soft); vertical-align: 1px; }
  .prompt { margin: 8px 0 0; padding: 8px 10px; background: var(--page); border-radius: var(--radius); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px; }
  .sact { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; font-size: 13px; }
  .sact button { font-size: 13px; padding: 3px 10px; }
  .sact:empty { display: none; }
  .result { font-size: 13px; }
  .result.ok { color: var(--moss); }
  .result.failed { color: var(--signal); }
  .findings li.card { display: block; padding: 0; }
  .doing { list-style: none; margin: 0; padding: 0; }
  .doing a { display: grid; grid-template-columns: 10px minmax(0, auto) 1fr; column-gap: 8px; align-items: baseline; padding: 6px 0; border-bottom: 1px solid var(--hairline); color: inherit; text-decoration: none; }
  .doing a:hover .dn { text-decoration: underline; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--hairline); align-self: center; }
  .dot.busy { background: var(--cobalt); }
  .dot.waiting { background: var(--signal); }
  .dn { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dm { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dt { grid-column: 2 / -1; font-size: 13px; color: var(--muted); }
  .meta { margin: 14px 0 0; }
</style>
