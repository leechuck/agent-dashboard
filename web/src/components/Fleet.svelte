<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import SessionRow from './SessionRow.svelte'
  import MachineHeader from './MachineHeader.svelte'
  import AttentionStrip from './AttentionStrip.svelte'
  import type { Session } from '../lib/types'

  let { selected }: { selected: string | undefined } = $props()
  let showFinished = $state(false)
  let showStale = $state<Record<string, boolean>>({})

  type Group = { machine: string; sessions: Session[]; stale: Session[] }
  const groups = $derived.by((): Group[] => {
    const byMachine = new Map<string, Group>()
    const get = (m: string) => {
      let g = byMachine.get(m)
      if (!g) byMachine.set(m, (g = { machine: m, sessions: [], stale: [] }))
      return g
    }
    for (const s of fleet.sessionList) {
      const g = get(s.machine)
      const finished = !['busy', 'idle', 'waiting'].includes(s.status)
      if (finished && !showFinished) continue
      if (Fleet.isStale(s)) {
        g.stale.push(s)
        if (!showStale[s.machine]) continue
      }
      g.sessions.push(s)
    }
    for (const m of Object.keys(fleet.machines)) get(m)
    return [...byMachine.values()].sort((a, b) => a.machine.localeCompare(b.machine))
  })
  import { Fleet } from '../lib/store.svelte'
  let cleaning = $state('')
  let cleanMsg = $state<Record<string, string>>({})
  async function cleanUp(machine: string, keys: string[]) {
    cleaning = machine
    try {
      const r = await fleet.cleanup(machine, keys)
      const failed = r.results.filter((x) => !x.ok).length
      cleanMsg[machine] = `Removed ${r.removed}${failed ? `, ${failed} could not be removed (not Claude background sessions)` : ''}.`
    } catch (e) {
      cleanMsg[machine] = String(e)
    } finally {
      cleaning = ''
    }
  }
</script>

<AttentionStrip />

{#if fleet.error}
  <p class="notice">Could not reach the hub: {fleet.error}</p>
{:else if fleet.loaded && groups.length === 0}
  <p class="notice">No machine has connected yet. Start a node with <code>agentdash node</code>.</p>
{/if}

{#each groups as g (g.machine)}
  <MachineHeader id={g.machine} count={g.sessions.length} />
  {#if g.sessions.length === 0}
    <p class="empty muted">Nothing running here.</p>
  {:else}
    <ul>
      {#each g.sessions as s (s.key)}
        <SessionRow session={s} selected={s.key === selected} />
      {/each}
    </ul>
  {/if}
  {#if g.stale.length > 0}
    <div class="stale small">
      <span class="muted">{g.stale.length} stale (untouched for 2 days)</span>
      <button onclick={() => (showStale[g.machine] = !showStale[g.machine])}>{showStale[g.machine] ? 'Hide' : 'Show'}</button>
      <button class="danger" disabled={cleaning === g.machine} onclick={() => cleanUp(g.machine, g.stale.map((s) => s.key))}>
        {cleaning === g.machine ? 'Removing…' : 'Remove all'}
      </button>
      {#if cleanMsg[g.machine]}<span class="muted">{cleanMsg[g.machine]}</span>{/if}
    </div>
  {/if}
{/each}

<div class="foot">
  <button onclick={() => (showFinished = !showFinished)}>
    {showFinished ? 'Hide finished' : 'Show finished'}
  </button>
</div>

<style>
  ul { list-style: none; margin: 0; padding: 0; background: var(--surface); }
  .notice { padding: 16px; margin: 0; }
  .empty { padding: 10px 16px 14px; margin: 0; font-size: 13px; }
  .foot { padding: 16px; }
  .stale { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 16px 12px; }
  .stale button { font-size: 13px; padding: 3px 10px; }
</style>
