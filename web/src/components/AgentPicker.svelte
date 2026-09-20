<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import type { AgentChoice } from '../lib/types'

  // Harness, then where its tokens come from, then a model that source really offers.
  let { machine, value = $bindable(), lockHarness = false }: { machine: string; value: AgentChoice; lockHarness?: boolean } = $props()

  const cat = $derived(fleet.catalog?.machines[machine])
  const endpoints = $derived(fleet.catalog?.endpoints ?? [])
  const harnessNames: Record<string, string> = { claude: 'Claude Code', codex: 'Codex', pi: 'pi', opencode: 'opencode' }
  const harnesses = $derived(Object.entries(cat?.harnesses ?? Object.fromEntries((fleet.machines[machine]?.harnesses ?? ['claude']).map(h => [h, true]))).filter(([, ok]) => ok).map(([h]) => h))

  type Source = { id: string; label: string; disabled?: boolean }
  const sources = $derived.by((): Source[] => {
    const out: Source[] = [{ id: 'default|', label: value.harness === 'claude' ? 'as this machine is set up' : 'its own subscription / config' }]
    if (value.harness === 'claude') {
      for (const l of cat?.logins ?? []) out.push({ id: `login|${l.dir}`, label: l.logged_in ? `Claude ${l.name}: ${l.account}${quota(l.account)}` : `Claude login "${l.name}" (not logged in)`, disabled: !l.logged_in })
    }
    if (value.harness !== 'opencode') {
      for (const e of endpoints) {
        const usable = value.harness === 'claude' ? !!e.anthropic_base_url : !!e.base_url
        if (!usable) continue
        const st = cat?.endpoints?.find((x) => x.id === e.id)
        const why = !st ? '' : !st.key_present ? ` (no ${e.key_env} on ${machine})` : !st.reachable ? ' (not reachable from there)' : ''
        out.push({ id: `endpoint|${e.id}`, label: `${e.name || e.id}${why}`, disabled: !!why })
      }
    }
    return out
  })
  const source = $derived(`${value.backend}|${value.backend === 'login' ? value.login : value.backend === 'endpoint' ? value.endpoint : ''}`)
  function quota(account: string): string {
    const ws = fleet.usage.filter(w => w.provider === 'anthropic' && w.account === account && ['session', 'five_hour', 'weekly', 'seven_day'].includes(w.window))
    return ws.length ? ' · ' + ws.map(w => `${w.label || w.window}: ${w.used_pct.toFixed(0)}% used`).join(', ') : ''
  }
  function setSource(id: string) {
    const [backend, ref] = id.split('|')
    value.backend = backend as AgentChoice['backend']
    value.login = backend === 'login' ? ref : ''
    value.endpoint = backend === 'endpoint' ? ref : ''
    value.model = backend === 'endpoint' ? (models[0]?.id ?? '') : ''
    custom = false
  }

  const models = $derived.by(() => {
    if (value.backend === 'endpoint') {
      const st = cat?.endpoints?.find((x) => x.id === value.endpoint)
      const fixed = endpoints.find((e) => e.id === value.endpoint)?.models ?? []
      return (st?.models?.length ? st.models : fixed).map((m) => ({ id: m, label: m }))
    }
    const offered = cat?.models?.[value.harness]
    return offered?.length ? offered : [{ id: '', label: 'as configured' }]
  })
  const efforts = $derived(models.find((m) => m.id === value.model && 'efforts' in m && (m as any).efforts?.length) ? ((models.find((m) => m.id === value.model) as any).efforts as string[]) : (cat?.efforts?.[value.harness] ?? []))
  let custom = $state(false)
  $effect(() => {
    // an endpoint always needs a named model; take its first once the list is known
    if (value.backend === 'endpoint' && !value.model && models.length) value.model = models[0].id
  })
  function setHarness(h: string) {
    value.harness = h
    value.backend = 'default'
    value.login = value.endpoint = value.model = value.effort = ''
    value.work_mode = ''
    value.permissions = 'default'
    custom = false
  }
</script>

<div class="picker">
  <label>Agent
    <select value={value.harness} onchange={(e) => setHarness(e.currentTarget.value)} disabled={lockHarness}>
      {#each harnesses as h}<option value={h}>{harnessNames[h] ?? h}</option>{/each}
    </select>
  </label>
  <label>Subscription / provider
    <select value={source} onchange={(e) => setSource(e.currentTarget.value)}>
      {#each sources as s (s.id)}<option value={s.id} disabled={s.disabled}>{s.label}</option>{/each}
    </select>
  </label>
  <label>Model
    {#if custom}
      <span class="row"><input bind:value={value.model} placeholder="exact model id" /><button type="button" onclick={() => { custom = false; value.model = models[0]?.id ?? '' }}>list</button></span>
    {:else}
      <select value={value.model} onchange={(e) => { if (e.currentTarget.value === '__other') { custom = true; value.model = '' } else value.model = e.currentTarget.value }}>
        {#each models as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
        {#if value.model && !models.some((m) => m.id === value.model)}<option value={value.model}>{value.model}</option>{/if}
        <option value="__other">other…</option>
      </select>
    {/if}
  </label>
  {#if efforts.length}
    <label>Thinking
      <select bind:value={value.effort}>
        <option value="">as configured</option>
        {#each efforts as e}<option value={e}>{e}</option>{/each}
      </select>
    </label>
  {/if}
  {#if ['claude', 'codex', 'opencode'].includes(value.harness)}
    <label>Mode
      <select value={value.work_mode ?? ''} onchange={(e) => { value.work_mode = e.currentTarget.value as AgentChoice['work_mode']; if (value.permissions === 'plan') value.permissions = 'default' }}>
        <option value="">As configured</option>
        <option value="plan">Planning</option>
        <option value="implement">Implementation</option>
      </select>
    </label>
  {:else}
    <p class="small muted">This agent has no built-in planning mode available here.</p>
  {/if}
  {#if value.harness === 'claude' || value.harness === 'codex'}
    <label>Permissions
      <select bind:value={value.permissions} disabled={value.work_mode === 'plan' && value.harness === 'claude'}>
        <option value="default">as configured</option>
        {#if value.harness === 'claude'}
          <option value="acceptEdits">edit files, ask for the rest</option>
        {/if}
        <option value="bypass">without asking (bypass)</option>
      </select>
    </label>
  {/if}
</div>
{#if !cat}<p class="small muted">Reading what {machine || 'the machine'} can run…</p>{/if}

<style>
  .picker { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px 14px; }
  label { display: grid; gap: 3px; font-size: 13px; color: var(--muted); min-width: 0; }
  select, input { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); color: var(--ink); font: inherit; font-size: 14px; min-width: 0; width: 100%; }
  .row { display: flex; gap: 6px; }
  .row button { font-size: 12px; padding: 2px 8px; }
</style>
