<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import { ago } from '../lib/format'
  let { id, count }: { id: string; count: number } = $props()
  const m = $derived(fleet.machines[id])
</script>

<div class="mh">
  <span class="name">{id}</span>
  {#if m && !m.online}
    <span class="state off">node offline{m.last_seen ? `, last seen ${ago(m.last_seen)} ago` : ''}</span>
  {:else}
    <span class="state">{count} session{count === 1 ? '' : 's'}</span>
  {/if}
</div>

<style>
  .mh {
    position: sticky;
    top: 48px;
    z-index: 4;
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 14px 16px 6px;
    background: var(--page);
    border-bottom: 1px solid var(--hairline);
  }
  .name { font-weight: 600; font-size: 17px; }
  .state { color: var(--muted); font-size: 13px; }
  .state.off { color: var(--signal); }
</style>
