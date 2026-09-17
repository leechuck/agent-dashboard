<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import type { UsageWindow } from '../lib/types'

  let history = $state<Record<string, { t: number; pct: number }[]>>({})
  let now = $state(Date.now())
  const windows = $derived(fleet.usage)

  const names: Record<string, string> = { anthropic: 'Claude', openai: 'Codex', openrouter: 'OpenRouter' }
  const plans: Record<string, string> = { max: 'Max plan', pro: 'Pro plan', plus: 'Plus plan' }

  type Kind = 'session' | 'week' | 'money'
  function kindOf(w: UsageWindow): Kind {
    if (w.provider === 'openrouter' || w.window === 'extra_usage' || w.window === 'credits') return 'money'
    if (w.window === 'five_hour' || /_300m$/.test(w.window)) return 'session'
    return 'week'
  }

  const subs = $derived.by(() => {
    const out: { provider: string; account: string; machine: string; session?: UsageWindow; week: UsageWindow[]; money: UsageWindow[] }[] = []
    for (const p of ['anthropic', 'openai']) {
      const ws = windows.filter((w) => w.provider === p)
      if (!ws.length) continue
      out.push({
        provider: p,
        account: ws[0].account,
        machine: ws[0].machine,
        session: ws.find((w) => kindOf(w) === 'session'),
        week: ws.filter((w) => kindOf(w) === 'week'),
        money: ws.filter((w) => kindOf(w) === 'money'),
      })
    }
    return out
  })
  const openrouter = $derived(windows.filter((w) => w.provider === 'openrouter'))
  const orCredits = $derived(openrouter.find((w) => w.window === 'credits'))
  const orKey = $derived(openrouter.find((w) => w.window === 'key_limit'))

  function countdown(ms: number | null): string {
    if (!ms) return ''
    const d = ms - now
    if (d <= 0) return 'resetting now'
    const h = Math.floor(d / 3600000)
    const m = Math.floor((d % 3600000) / 60000)
    if (h >= 48) return `resets in ${Math.round(h / 24)} days`
    if (h >= 1) return `resets in ${h} h ${m} min`
    return `resets in ${m} min`
  }
  function at(ms: number | null): string {
    if (!ms) return ''
    return new Date(ms).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  }
  function tone(p: number): string {
    return p >= 95 ? 'crit' : p >= 80 ? 'warn' : 'ok'
  }
  function usd(v: unknown): string {
    const n = Number(v)
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : '?'
  }
  function path(pts: { t: number; pct: number }[]): string {
    if (pts.length < 2) return ''
    const t0 = pts[0].t
    const span = Math.max(1, pts[pts.length - 1].t - t0)
    return pts.map((p, i) => `${i ? 'L' : 'M'}${((p.t - t0) / span) * 100},${100 - p.pct}`).join(' ')
  }
  const key = (w: UsageWindow) => `${w.provider}:${w.window}`

  async function loadHistory() {
    for (const w of windows) {
      try {
        history[key(w)] = await api.usageHistory(w.provider, w.window)
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

{#snippet gauge(w: UsageWindow | undefined, title: string)}
  <div class={`gauge ${w ? tone(w.used_pct) : 'none'}`}>
    <div class="gt">{title}</div>
    {#if w}
      <div class="big">{w.used_pct.toFixed(0)}<span class="unit">%</span></div>
      <div class="bar"><div class="fill" style={`width:${Math.min(100, w.used_pct)}%`}></div></div>
      <div class="small muted">{(100 - w.used_pct).toFixed(0)}% left{w.resets_at ? ` · ${countdown(w.resets_at)}` : ''}</div>
      {#if w.resets_at}<div class="small muted">{at(w.resets_at)}</div>{/if}
      {#if history[key(w)]?.length > 1}
        <svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none"><path d={path(history[key(w)])} /></svg>
      {/if}
    {:else}
      <div class="small muted">not reported for this plan</div>
    {/if}
  </div>
{/snippet}

<section>
  <h1>Limits</h1>
  {#if windows.length === 0}
    <p class="notice muted">No usage data yet. Nodes report every ten minutes.</p>
  {/if}

  {#each subs as s (s.provider)}
    <div class="prov">
      <div class="phead">
        <span class="pname">{names[s.provider] ?? s.provider}</span>
        <span class="muted small">{plans[s.account] ?? s.account}{s.account ? ' · ' : ''}via {s.machine}</span>
      </div>
      <div class="pair">
        {@render gauge(s.session, 'Session · rolling 5 hours')}
        {@render gauge(s.week[0], s.week[0]?.label && s.week[0].label !== '7 days' ? s.week[0].label : 'Week · rolling 7 days')}
      </div>
      {#if s.week.length > 1}
        <div class="pair">
          {#each s.week.slice(1) as w (w.window)}
            {@render gauge(w, w.label)}
          {/each}
        </div>
      {/if}
      {#each s.money as w (w.window)}
        <div class="money small">
          <span>Extra usage this month</span>
          <span class="muted">{usd(w.detail?.used)} of {usd(w.detail?.limit)} ({w.used_pct.toFixed(0)}%)</span>
        </div>
      {/each}
    </div>
  {/each}

  {#if openrouter.length}
    <div class="prov">
      <div class="phead">
        <span class="pname">OpenRouter</span>
        <span class="muted small">pay as you go · via {openrouter[0].machine}</span>
      </div>
      {#if orCredits}
        <div class="row">
          <div class="rt">Credits on the account</div>
          <div class="amount">{usd(orCredits.detail?.remaining)} <span class="muted small">left of {usd(orCredits.detail?.total)} bought</span></div>
          <div class="bar thin"><div class="fill neutral" style={`width:${Math.min(100, 100 - orCredits.used_pct)}%`}></div></div>
          <div class="small muted">{usd(orCredits.detail?.used)} spent in total. Top up at openrouter.ai when this runs low.</div>
        </div>
      {/if}
      {#if orKey}
        <div class="row">
          <div class="rt">Spending cap on this API key</div>
          {#if orKey.detail?.limit}
            <div class="amount">{usd(Number(orKey.detail.limit) - Number(orKey.detail.remaining ?? 0))} <span class="muted small">of {usd(orKey.detail.limit)} cap{orKey.label.includes('no reset') ? ', never resets' : `, resets ${orKey.label.replace(/.*\((.*)\)/, '$1')}`}</span></div>
            <div class="bar thin"><div class={`fill ${tone(orKey.used_pct)}`} style={`width:${Math.min(100, orKey.used_pct)}%`}></div></div>
          {:else}
            <div class="small muted">No cap set on this key; only the credit balance limits spending.</div>
          {/if}
          <div class="small muted">
            Spent {usd(orKey.detail?.usage_daily)} today · {usd(orKey.detail?.usage_weekly)} this week · {usd(orKey.detail?.usage_monthly)} this month
          </div>
        </div>
      {/if}
    </div>
  {/if}
</section>

<style>
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .notice { padding: 0 16px; }
  .prov { background: var(--surface); border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); margin-bottom: 14px; padding: 12px 16px 12px; }
  .phead { display: flex; gap: 10px; align-items: baseline; margin-bottom: 10px; flex-wrap: wrap; }
  .pname { font-weight: 600; font-size: 17px; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 8px; }
  .gauge { padding: 10px 12px; border: 1px solid var(--hairline); border-radius: var(--radius); display: grid; gap: 4px; align-content: start; }
  .gauge.none { color: var(--muted); }
  .gt { font-size: 13px; color: var(--muted); }
  .big { font-size: 30px; line-height: 1.15; font-weight: 600; font-variant-numeric: tabular-nums; }
  .unit { font-size: 15px; font-weight: 400; color: var(--muted); margin-left: 2px; }
  .bar { height: 6px; background: var(--page); border-radius: 3px; overflow: hidden; }
  .bar.thin { height: 4px; margin: 6px 0; }
  .fill { height: 100%; background: var(--cobalt); }
  .fill.neutral { background: var(--moss); }
  .warn .fill, .fill.warn { background: var(--amber); }
  .crit .fill, .fill.crit { background: var(--signal); }
  .crit .big { color: var(--signal); }
  .warn .big { color: var(--amber); }
  .spark { width: 100%; height: 22px; margin-top: 2px; }
  .spark path { fill: none; stroke: var(--muted); stroke-width: 3; vector-effect: non-scaling-stroke; }
  .money { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-top: 1px solid var(--hairline); }
  .row { padding: 8px 0; border-top: 1px solid var(--hairline); display: grid; gap: 2px; }
  .row:first-of-type { border-top: none; }
  .rt { font-weight: 500; }
  .amount { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  @media (max-width: 420px) { .pair { grid-template-columns: 1fr; } }
</style>
