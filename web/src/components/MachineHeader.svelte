<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import { ago, clock } from '../lib/format'
  let { id, count }: { id: string; count: number } = $props()
  const m = $derived(fleet.machines[id])
  const armed = $derived(!!m && m.armed && (m.armed_until === 0 || m.armed_until > Date.now()))
  let busy = $state(false)
  async function toggle() {
    busy = true
    try {
      await fleet.arm(id, !armed)
    } finally {
      busy = false
    }
  }
</script>

<div class="mh">
  <span class="name">{id}</span>
  {#if m && !m.online}
    <span class="state off">node offline{m.last_seen ? `, last seen ${ago(m.last_seen)} ago` : ''}</span>
  {:else}
    <span class="state">{count} session{count === 1 ? '' : 's'}</span>
  {/if}
  <a class="newbtn" href={`#/new?machine=${encodeURIComponent(id)}`} title="Start a background session here">+ new</a>
  <button class="arm" class:on={armed} disabled={busy || !m?.online} onclick={toggle}
    title={armed ? 'Approvals go to your phone. Click to send them back to the terminal.' : 'Send approvals to your phone'}>
    {#if armed}
      to phone{m.armed_until ? ` until ${clock(m.armed_until)}` : ''}
    {:else}
      approvals: terminal
    {/if}
  </button>
</div>

<style>
  .mh {
    position: sticky;
    top: 48px;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px 6px;
    background: var(--page);
    border-bottom: 1px solid var(--hairline);
  }
  .name { font-weight: 600; font-size: 17px; }
  .state { color: var(--muted); font-size: 13px; }
  .state.off { color: var(--signal); }
  .newbtn { margin-left: auto; font-size: 13px; }
  .arm { font-size: 13px; padding: 4px 10px; color: var(--muted); }
  .arm.on { color: var(--signal); border-color: var(--signal); background: var(--signal-soft); }
</style>
