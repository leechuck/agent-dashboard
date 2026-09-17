<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import SessionRow from './SessionRow.svelte'
  import SessionCard from './SessionCard.svelte'
  import MachineHeader from './MachineHeader.svelte'
  import AttentionStrip from './AttentionStrip.svelte'
  import type { Session } from '../lib/types'

  // board: large cards filling the page (home); rail: compact rows beside an open page
  let { selected, cockpitCard = false, layout = 'rail' }: { selected: string | undefined; cockpitCard?: boolean; layout?: 'board' | 'rail' } = $props()
  const ck = $derived(fleet.cockpit)
  const ckActs = $derived(ck?.findings.filter((f) => f.severity === 'act').length ?? 0)
  const ckWarns = $derived(ck?.findings.filter((f) => f.severity === 'warn').length ?? 0)
  let showFinished = $state(false)
  let showStale = $state<Record<string, boolean>>({})
  let openKids = $state<Record<string, boolean>>({})

  type Group = { machine: string; sessions: Session[]; stale: Session[]; kids: Record<string, Session[]> }
  const groups = $derived.by((): Group[] => {
    const byMachine = new Map<string, Group>()
    const get = (m: string) => {
      let g = byMachine.get(m)
      if (!g) byMachine.set(m, (g = { machine: m, sessions: [], stale: [], kids: {} }))
      return g
    }
    for (const s of fleet.sessionList) {
      const g = get(s.machine)
      // a sub-agent lives under the session that started it, as long as that one is listed
      const parentKey = (s.extra as Record<string, any> | undefined)?.parent as string | undefined
      if (parentKey && fleet.sessions[parentKey] && ['busy', 'idle', 'waiting'].includes(fleet.sessions[parentKey].status)) {
        if (['busy', 'idle', 'waiting'].includes(s.status) || showFinished) (g.kids[parentKey] ??= []).push(s)
        continue
      }
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

{#if cockpitCard && ck}
  <a class="ck" class:hot={ckActs > 0} class:calm={ckActs === 0 && ckWarns === 0} href="#/overview">
    <span class="ckh">{ck.headline.split('. ')[0]}</span>
    <span class="cks small muted">{ck.stats.busy} working · {ck.stats.idle} idle</span>
    <span class="ckgo">Overview ›</span>
  </a>
{/if}
<AttentionStrip />

{#if fleet.error}
  <p class="notice">Could not reach the hub: {fleet.error}</p>
{:else if fleet.loaded && groups.length === 0}
  <p class="notice">No machine has connected yet. Start a node with <code>agentdash node</code>.</p>
{/if}

{#each groups as g (g.machine)}
  <MachineHeader id={g.machine} count={g.sessions.length} subagents={Object.values(g.kids).reduce((n, k) => n + k.length, 0)} />
  {#if g.sessions.length === 0}
    <p class="empty muted">Nothing running here.</p>
  {:else if layout === 'board'}
    <div class="board">
      {#each g.sessions as s (s.key)}
        <SessionCard session={s} kids={g.kids[s.key] ?? []} selected={s.key === selected} />
      {/each}
    </div>
  {:else}
    <ul>
      {#each g.sessions as s (s.key)}
        {@const kids = g.kids[s.key] ?? []}
        <SessionRow session={s} selected={s.key === selected} />
        {#if kids.length}
          {@const working = kids.filter((k) => k.status === 'busy').length}
          {@const blocked = kids.filter((k) => k.status === 'waiting').length}
          <li class="kids">
            <button class="kt" class:open={!!openKids[s.key]} onclick={() => (openKids[s.key] = !openKids[s.key])} aria-expanded={!!openKids[s.key]}>
              <span class="tw">{openKids[s.key] ? '▾' : '▸'}</span>
              {kids.length} sub-agent{kids.length === 1 ? '' : 's'}
              <span class="muted">{working ? `${working} working` : 'none working'}</span>
              {#if blocked}<span class="blocked">{blocked} waiting</span>{/if}
            </button>
            {#if openKids[s.key]}
              <ul>
                {#each kids as k (k.key)}
                  <SessionRow session={k} selected={k.key === selected} child />
                {/each}
              </ul>
              {#if kids.length > 2}
                <button class="kt end" onclick={() => (openKids[s.key] = false)}><span class="tw">▴</span> Hide {kids.length} sub-agents</button>
              {/if}
            {/if}
          </li>
        {/if}
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
  .ck { display: grid; grid-template-columns: 1fr auto; gap: 0 12px; align-items: center; padding: 10px 16px 10px 12px; background: var(--surface); border-bottom: 1px solid var(--hairline); border-left: 4px solid var(--amber); color: inherit; text-decoration: none; }
  .ck:hover { text-decoration: none; background: var(--page); }
  .ck.hot { border-left-color: var(--signal); }
  .ck.calm { border-left-color: var(--moss); }
  .ckh { font-weight: 600; }
  .cks { grid-column: 1; }
  .ckgo { grid-column: 2; grid-row: 1 / span 2; color: var(--cobalt); font-size: 13px; white-space: nowrap; }
  .kids { border-bottom: 1px solid var(--hairline); background: var(--page); }
  .kt { display: flex; gap: 8px; align-items: center; width: 100%; border: 0; border-radius: 0; background: none; padding: 5px 16px 5px 17px; font-size: 13px; text-align: left; color: var(--ink); }
  .kt:hover { background: var(--surface); }
  /* an open list can be long: keep its handle in reach under the machine header */
  .kt.open { position: sticky; top: calc(var(--sticky-top, 48px) + 43px); z-index: 3; background: var(--page); border-bottom: 1px solid var(--hairline); }
  .kt.end { color: var(--muted); border-top: 1px solid var(--hairline); }
  .tw { width: 10px; color: var(--muted); }
  .blocked { color: var(--signal); font-weight: 500; }
  .kids ul { background: var(--page); }
  .board { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 12px; padding: 12px 16px 16px; align-items: stretch; }
  .notice { padding: 16px; margin: 0; }
  .empty { padding: 10px 16px 14px; margin: 0; font-size: 13px; }
  .foot { padding: 16px; }
  .stale { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 16px 12px; }
  .stale button { font-size: 13px; padding: 3px 10px; }
</style>
