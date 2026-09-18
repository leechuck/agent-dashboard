<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  let open = $state(false)
  const names: Record<string, string> = { anthropic: 'Claude', openai: 'Codex', openrouter: 'OpenRouter' }
  const plans = $derived.by(() => {
    const map = new Map<string, {provider: string; account: string; windows: typeof fleet.usage}>()
    for (const w of fleet.usage) {
      const key = `${w.provider}:${w.account}`
      if (!map.has(key)) map.set(key, {provider: w.provider, account: w.account, windows: []})
      map.get(key)!.windows.push(w)
    }
    for (const machine of Object.values(fleet.catalog?.machines ?? {})) {
      for (const login of machine.logins ?? []) {
        if (login.account && !map.has(`anthropic:${login.account}`)) map.set(`anthropic:${login.account}`, {provider:'anthropic', account:login.account, windows:[]})
      }
    }
    return [...map.values()].sort((a,b) => `${a.provider}:${a.account}`.localeCompare(`${b.provider}:${b.account}`))
  })
  const high = $derived(plans.filter(p => p.windows.some(w => w.used_pct >= 80)).length)
  function toggle() { open = !open; if (open) void fleet.loadCatalog() }
</script>
<svelte:window onkeydown={(e) => { if (e.key === 'Escape') open = false }} />
<button class="trigger" class:warn={high > 0} aria-expanded={open} aria-controls="limit-summary" onclick={toggle}>Limits{high ? ` · ${high} high` : ''} ▾</button>
{#if open}
  <div class="backdrop" onclick={() => open = false} role="presentation"></div>
  <section id="limit-summary" aria-label="Usage by plan">
    <div class="heading"><b>Usage by plan</b><button onclick={() => open = false} aria-label="Close limits">×</button></div>
    <p class="small muted">Each percentage is a separate quota used.</p>
    {#each plans as p}
      <article><b>{names[p.provider] ?? p.provider} · {p.account || 'Default account'}</b>
        {#each p.windows as w}
          <div class="quota"><span>{w.label || w.window}</span><strong class:warn={w.used_pct >= 80}>{w.used_pct.toFixed(0)}%</strong></div>
          {#if Date.now() - w.fetched_at > 15 * 60 * 1000}<div class="small muted">Last read {new Date(w.fetched_at).toLocaleString()}</div>{/if}
        {:else}<p class="small muted">Usage unavailable</p>{/each}
      </article>
    {:else}<p>No usage readings available.</p>{/each}
    <a href="#/limits" onclick={() => open = false}>All limits, reset times and logins →</a>
  </section>
{/if}
<style>
  .trigger { padding: 0; border: 0; background: transparent; color: var(--muted); font: inherit; }
  .warn { color: var(--amber); }
  .backdrop { position: fixed; inset: 48px 0 0; z-index: 9; }
  section { position: fixed; top: 48px; right: 12px; width: min(410px, calc(100vw - 24px)); max-height: calc(100dvh - 64px); overflow-y: auto; white-space: normal; background: var(--surface); color: var(--ink); border: 1px solid var(--hairline); border-radius: var(--radius); padding: 16px; box-shadow: 0 8px 24px #0003; z-index: 10; }
  .heading, .quota { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
  .heading button { padding: 0 8px; }
  article { padding: 12px 0; border-top: 1px solid var(--hairline); }
  .quota { margin-top: 6px; }
</style>
