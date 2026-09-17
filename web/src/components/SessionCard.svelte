<script lang="ts">
  import type { Session } from '../lib/types'
  import { fleet } from '../lib/store.svelte'
  import { ago, backendLabel, contextOf, displayName, modelLabel, shortCwd, statusLabel } from '../lib/format'
  let { session, kids = [], selected = false }: { session: Session; kids?: Session[]; selected?: boolean } = $props()
  const s = $derived(session)
  const x = $derived((s.extra ?? {}) as Record<string, any>)
  const ctx = $derived(contextOf(s))
  const model = $derived(modelLabel(s))
  const stale = $derived(s.status !== 'busy' && Date.now() - s.updated_at > 48 * 3600 * 1000)
  const working = $derived(kids.filter((k) => k.status === 'busy').length)
  const blocked = $derived(kids.filter((k) => k.status === 'waiting').length)
  let showKids = $state(false)
  /** What it was last asked, unless that is just "continue": then the standing goal or first request. */
  const asked = $derived.by(() => {
    const last = String(x.last_user ?? '')
    if (x.goal) return { label: 'goal', text: String(x.goal) }
    if (last.length > 25) return { label: 'asked', text: last }
    return x.first_user ? { label: 'task', text: String(x.first_user) } : last ? { label: 'asked', text: last } : null
  })
  const href = (k: string) => `#/session/${encodeURIComponent(k)}`
</script>

<article class={`card ${s.status}`} class:stale class:selected>
  <a class="main" href={href(s.key)} onpointerenter={() => fleet.openSession(s.key)} ontouchstart={() => fleet.openSession(s.key)}>
    <header>
      <span class={`dot ${s.status}`}></span>
      <span class="state">{stale ? 'stale' : statusLabel(s.status, s.waiting_for, !!x.goal)}</span>
      {#if fleet.drafts[s.key]}<span class="draft" title="You have unsent text for this session">draft</span>{/if}
      <span class="age">{ago(s.updated_at)}</span>
    </header>
    <h3>{displayName(s)}</h3>
    <div class="where">{backendLabel(s)}{x.tmux && s.harness !== 'tmux' ? ' · tmux' : ''} · {shortCwd(s.cwd)}</div>
    {#if asked}<p class="asked"><span class="lbl">{asked.label}</span> {asked.text}</p>{/if}
    {#if s.last_line}<p class="last">{s.last_line}</p>{/if}
    <footer>
      {#if model}<span>{model}{x.fast_mode ? ' · fast' : ''}</span>{/if}
      {#if x.effort}<span>think {x.effort}</span>{/if}
      {#if ctx}
        <span class="ctx" class:amber={ctx.pct >= 70} class:red={ctx.pct >= 85} title={`context window${ctx.window ? ` of ${ctx.window}` : ''}`}>
          <span class="cbar"><span style={`width:${Math.min(100, ctx.pct)}%`}></span></span>{ctx.pct.toFixed(0)}%
        </span>
      {/if}
    </footer>
  </a>
  {#if kids.length}
    <button class="kids" onclick={() => (showKids = !showKids)} aria-expanded={showKids}>
      <span>{showKids ? '▾' : '▸'} {kids.length} sub-agent{kids.length === 1 ? '' : 's'}</span>
      <span class="muted">{working ? `${working} working` : 'none working'}</span>
      {#if blocked}<span class="blocked">{blocked} waiting</span>{/if}
    </button>
    {#if showKids}
      <ul>
        {#each kids as k (k.key)}
          <li><a href={href(k.key)}><span class={`dot ${k.status}`}></span><span class="kn">{displayName(k)}</span><span class="muted">{ago(k.updated_at)}</span></a></li>
        {/each}
      </ul>
    {/if}
  {/if}
</article>

<style>
  .card { display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--hairline); border-top: 3px solid var(--hairline); border-radius: var(--radius); min-width: 0; transition: transform .12s ease, box-shadow .12s ease; }
  .card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgb(0 0 0 / .08); }
  .card.busy { border-top-color: var(--cobalt); }
  .card.waiting { border-top-color: var(--signal); background: linear-gradient(var(--signal-soft), var(--surface) 64px); }
  .card.stale { border-top-style: dashed; opacity: .7; }
  .card.selected { outline: 2px solid var(--cobalt); }
  .main { display: flex; flex-direction: column; gap: 5px; padding: 12px 14px 12px; color: inherit; text-decoration: none; flex: 1; min-width: 0; }
  .main:hover { text-decoration: none; }
  header { display: flex; align-items: center; gap: 7px; font-size: 12.5px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--hairline); flex: none; }
  .dot.busy { background: var(--cobalt); animation: beat 1.8s ease-in-out infinite; }
  .dot.waiting { background: var(--signal); }
  .dot.idle { background: var(--moss); }
  .state { font-weight: 600; letter-spacing: .02em; color: var(--muted); }
  .busy .state { color: var(--cobalt); }
  .waiting .state { color: var(--signal); }
  .draft { font-size: 10.5px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--amber); background: var(--amber-soft); padding: 0 6px; border-radius: 8px; }
  .age { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
  h3 { margin: 2px 0 0; font-size: 17px; line-height: 1.25; font-weight: 600; letter-spacing: -.005em; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .where { font-size: 12.5px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  p { margin: 3px 0 0; font-size: 13.5px; line-height: 1.4; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
  .asked { -webkit-line-clamp: 2; line-clamp: 2; }
  .lbl { font-size: 10.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin-right: 4px; }
  .last { -webkit-line-clamp: 3; line-clamp: 3; color: var(--muted); border-left: 2px solid var(--hairline); padding-left: 8px; }
  footer { display: flex; flex-wrap: wrap; gap: 3px 12px; margin-top: auto; padding-top: 8px; font-size: 12px; color: var(--muted); }
  .ctx { display: inline-flex; align-items: center; gap: 5px; font-variant-numeric: tabular-nums; }
  .cbar { width: 44px; height: 4px; border-radius: 2px; background: var(--hairline); overflow: hidden; }
  .cbar span { display: block; height: 100%; background: var(--muted); }
  .ctx.amber { color: var(--amber); } .ctx.amber .cbar span { background: var(--amber); }
  .ctx.red { color: var(--signal); } .ctx.red .cbar span { background: var(--signal); }
  .kids { display: flex; gap: 8px; width: 100%; border: 0; border-top: 1px solid var(--hairline); border-radius: 0 0 var(--radius) var(--radius); background: var(--page); padding: 6px 14px; font-size: 12.5px; text-align: left; }
  .blocked { color: var(--signal); font-weight: 500; }
  ul { list-style: none; margin: 0; padding: 2px 0 6px; background: var(--page); border-radius: 0 0 var(--radius) var(--radius); max-height: 190px; overflow-y: auto; }
  li a { display: flex; gap: 8px; align-items: center; padding: 3px 14px; font-size: 12.5px; color: inherit; }
  .kn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @keyframes beat { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @media (prefers-reduced-motion: reduce) { .card, .dot.busy { transition: none; animation: none; } .card:hover { transform: none; } }
</style>
