<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import SessionRow from './SessionRow.svelte'
  import MachineHeader from './MachineHeader.svelte'
  import AttentionStrip from './AttentionStrip.svelte'
  import type { Session } from '../lib/types'

  let { selected }: { selected: string | undefined } = $props()
  let showFinished = $state(false)

  const groups = $derived.by(() => {
    const byMachine = new Map<string, Session[]>()
    for (const s of fleet.sessionList) {
      if (!showFinished && !['busy', 'idle', 'waiting'].includes(s.status)) continue
      const list = byMachine.get(s.machine) ?? []
      list.push(s)
      byMachine.set(s.machine, list)
    }
    for (const m of Object.keys(fleet.machines)) if (!byMachine.has(m)) byMachine.set(m, [])
    return [...byMachine.entries()].sort(([a], [b]) => a.localeCompare(b))
  })
</script>

<AttentionStrip />

{#if fleet.error}
  <p class="notice">Could not reach the hub: {fleet.error}</p>
{:else if fleet.loaded && groups.length === 0}
  <p class="notice">No machine has connected yet. Start a node with <code>agentdash node</code>.</p>
{/if}

{#each groups as [machine, sessions] (machine)}
  <MachineHeader id={machine} count={sessions.length} />
  {#if sessions.length === 0}
    <p class="empty muted">Nothing running here.</p>
  {:else}
    <ul>
      {#each sessions as s (s.key)}
        <SessionRow session={s} selected={s.key === selected} />
      {/each}
    </ul>
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
</style>
