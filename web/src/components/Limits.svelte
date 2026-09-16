<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import type { UsageWindow } from '../lib/types'

  let history = $state<Record<string, { t: number; pct: number }[]>>({})
  let now = $state(Date.now())
  const windows = $derived(fleet.usage)

  const providers = $derived.by(() => {
    const g = new Map<string, UsageWindow[]>()
    for (const w of windows) g.set(w.provider, [...(g.get(w.provider) ?? []), w])
    return [...g.entries()]
  })

  const names: Record<string, string> = { anthropic: 'Claude', openai: 'Codex', openrouter: 'OpenRouter' }

  function countdown(ms: number | null): string {
    if (!ms) return ''
    const d = ms - now
    if (d <= 0) return 'resetting'
    const h = Math.floor(d / 3600000)
    const m = Math.floor((d % 3600000) / 60000)
    if (h >= 48) return `resets in ${Math.round(h / 24)} d`
    return `resets in ${h} h ${m} min`
  }
  function at(ms: number | null): string {
    if (!ms) return ''
    return new Date(ms).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  }
  function tone(p: number): string {
    return p >= 95 ? 'crit' : p >= 80 ? 'warn' : 'ok'
  }
  function path(pts: { t: number; pct: number }[]): string {
    if (pts.length < 2) return ''
    const t0 = pts[0].t
    const span = Math.max(1, pts[pts.length - 1].t - t0)
    return pts.map((p, i) => `${i ? 'L' : 'M'}${((p.t - t0) / span) * 100},${100 - p.pct}`).join(' ')
  }

  async function loadHistory() {
    for (const w of windows) {
      const k = `${w.provider}:${w.window}`
      try {
        history[k] = await api.usageHistory(w.provider, w.window)
      } catch {
        /* ignore */
      }
    }
  }

  onMount(() => {
    loadHistory()
    const t = setInterval(() => (now = Date.now()), 30000)
    return () => clearInterval(t)
  })
  $effect(() => {
    windows.length
    loadHistory()
  })
</script>

<section>
  <h1>Limits</h1>
  {#if windows.length === 0}
    <p class="notice muted">No usage data yet. Nodes report every ten minutes. For Claude the quickest source is the status line sidecar: run <code>agentdash install statusline</code> on each machine.</p>
  {/if}
  {#each providers as [provider, ws] (provider)}
    <div class="prov">
      <div class="phead">
        <span class="pname">{names[provider] ?? provider}</span>
        {#if ws[0].account}<span class="muted small">{ws[0].account}</span>{/if}
        <span class="muted small">via {ws[0].machine}</span>
      </div>
      {#each ws as w (w.window)}
        <div class={`win ${tone(w.used_pct)}`}>
          <div class="wtop">
            <span class="wl">{w.label}</span>
            <span class="pct">{w.used_pct.toFixed(0)}%</span>
          </div>
          <div class="bar"><div class="fill" style={`width:${Math.min(100, w.used_pct)}%`}></div></div>
          <div class="wbot small muted">
            {#if w.resets_at}<span>{countdown(w.resets_at)} · {at(w.resets_at)}</span>{/if}
            {#if w.detail?.remaining !== undefined}<span>{Number(w.detail.remaining).toFixed(2)} left of {w.detail.total ?? w.detail.limit}</span>{/if}
            {#if w.detail?.balance !== undefined}<span>balance {w.detail.balance}</span>{/if}
            {#if history[`${w.provider}:${w.window}`]?.length > 1}
              <svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none"><path d={path(history[`${w.provider}:${w.window}`])} /></svg>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/each}
</section>

<style>
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .notice { padding: 0 16px; }
  .prov { background: var(--surface); border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); margin-bottom: 14px; padding: 10px 16px 6px; }
  .phead { display: flex; gap: 10px; align-items: baseline; margin-bottom: 6px; }
  .pname { font-weight: 600; font-size: 17px; }
  .win { padding: 8px 0; border-top: 1px solid var(--hairline); }
  .wtop { display: flex; justify-content: space-between; align-items: baseline; }
  .pct { font-weight: 600; font-variant-numeric: tabular-nums; }
  .bar { height: 6px; background: var(--page); border-radius: 3px; margin: 6px 0; overflow: hidden; }
  .fill { height: 100%; background: var(--cobalt); }
  .warn .fill { background: var(--amber); }
  .crit .fill { background: var(--signal); }
  .crit .pct { color: var(--signal); }
  .warn .pct { color: var(--amber); }
  .wbot { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .spark { width: 120px; height: 22px; margin-left: auto; }
  .spark path { fill: none; stroke: var(--muted); stroke-width: 3; vector-effect: non-scaling-stroke; }
</style>
