<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  const waiting = $derived(fleet.waiting.length)
</script>

<header>
  <a class="brand" href="#/">agentdash</a>
  <span class="link" class:live={fleet.connected} title={fleet.connected ? 'live updates on' : 'reconnecting'}></span>
  {#if waiting > 0}
    <a class="count" href="#/">{waiting} waiting</a>
  {/if}
</header>

<style>
  header {
    height: 48px;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    border-bottom: 1px solid var(--hairline);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 5;
  }
  .brand { font-weight: 600; color: var(--ink); letter-spacing: -0.01em; }
  .link { width: 8px; height: 8px; border-radius: 50%; background: var(--hairline); }
  .link.live { background: var(--moss); }
  .count { margin-left: auto; color: var(--signal); font-weight: 500; }
</style>
