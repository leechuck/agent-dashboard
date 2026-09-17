<script lang="ts">
  import type { Session } from '../lib/types'
  import { ago, backendLabel, contextOf, modelLabel, shortCwd, statusLabel } from '../lib/format'
  let { session, selected, child = false }: { session: Session; selected: boolean; child?: boolean } = $props()
  const s = $derived(session)
  const x = $derived((s.extra ?? {}) as Record<string, any>)
  const model = $derived(modelLabel(s))
  const ctx = $derived(contextOf(s))
  const stale = $derived(s.status !== 'busy' && Date.now() - s.updated_at > 48 * 3600 * 1000)
</script>

<li class={`row ${s.status}`} class:selected class:child>
  <a href={`#/session/${encodeURIComponent(s.key)}`}>
    <div class="top">
      <span class="name">{s.name || s.session_id.slice(0, 8)}</span>
      <span class="harness">{backendLabel(s)}{x.tmux && s.harness !== 'tmux' ? ' · tmux' : ''}</span>
      <span class="age muted">{ago(s.updated_at)}</span>
    </div>
    {#if !child}<div class="mid muted small">{shortCwd(s.cwd)}</div>{/if}
    {#if model || x.effort || ctx}
      <div class="facts small">
        {#if model}<span class="fact" title="model">{model}{x.fast_mode ? ' · fast' : ''}</span>{/if}
        {#if x.effort}<span class="fact" title="thinking depth">think {x.effort}</span>{/if}
        {#if ctx}
          <span class="fact ctx" class:amber={ctx.pct >= 70} class:red={ctx.pct >= 85} title={`context window${ctx.window ? ` of ${ctx.window}` : ''}`}>
            <span class="cbar"><span style={`width:${Math.min(100, ctx.pct)}%`}></span></span>{ctx.pct.toFixed(0)}%{ctx.window ? ` of ${ctx.window}` : ''}
          </span>
        {/if}
      </div>
    {/if}
    <div class="state small" class:stale>{stale ? `stale · ${statusLabel(s.status, s.waiting_for)} since ${ago(s.updated_at)}` : statusLabel(s.status, s.waiting_for)}</div>
    {#if s.last_line}
      <div class="last small">{s.last_line}</div>
    {/if}
  </a>
</li>

<style>
  .row { border-bottom: 1px solid var(--hairline); border-left: 3px solid var(--hairline); }
  .row.busy { border-left-color: var(--cobalt); animation: pulse 2.4s ease-in-out infinite; }
  .row.waiting { border-left-color: var(--signal); }
  .row.offline, .row.done, .row.stopped { border-left-style: dashed; }
  .row.selected { background: var(--cobalt-soft); }
  a { display: block; padding: 10px 16px 12px 13px; color: inherit; text-decoration: none; }
  a:hover { background: var(--page); text-decoration: none; }
  .top { display: flex; gap: 8px; align-items: baseline; }
  .name { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .harness { color: var(--muted); font-size: 13px; }
  .age { margin-left: auto; font-size: 13px; white-space: nowrap; flex: none; }
  .facts { display: flex; flex-wrap: wrap; gap: 3px 10px; margin-top: 3px; color: var(--muted); font-size: 12px; }
  .fact { white-space: nowrap; }
  .ctx { display: inline-flex; align-items: center; gap: 5px; font-variant-numeric: tabular-nums; }
  .cbar { width: 34px; height: 4px; border-radius: 2px; background: var(--hairline); overflow: hidden; }
  .cbar span { display: block; height: 100%; background: var(--muted); }
  .ctx.amber { color: var(--amber); } .ctx.amber .cbar span { background: var(--amber); }
  .ctx.red { color: var(--signal); } .ctx.red .cbar span { background: var(--signal); }
  .row.child { border-left-width: 2px; margin-left: 14px; background: var(--page); }
  .row.child a { padding-top: 6px; padding-bottom: 7px; }
  .row.child .name { font-weight: 500; font-size: 14px; }
  .state { margin-top: 2px; }
  .waiting .state { color: var(--signal); font-weight: 500; }
  .state.stale { color: var(--muted); font-weight: 400; }
  .row:has(.state.stale) { border-left-color: var(--hairline); border-left-style: dashed; animation: none; }
  .busy .state { color: var(--cobalt); }
  .last { margin-top: 4px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @keyframes pulse { 0%, 100% { border-left-color: var(--cobalt); } 50% { border-left-color: var(--cobalt-soft); } }
</style>
