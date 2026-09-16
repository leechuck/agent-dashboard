<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import { shortCwd } from '../lib/format'
  const waiting = $derived(fleet.waiting)
  const first = $derived(waiting[0])
</script>

{#if first}
  <div class="strip">
    <div class="n">{waiting.length}</div>
    <div class="body">
      <div class="lead">{waiting.length === 1 ? 'session is waiting for you' : 'sessions are waiting for you'}</div>
      <a class="which" href={`#/session/${encodeURIComponent(first.key)}`}>
        {first.name || first.session_id.slice(0, 8)} on {first.machine}
        {#if first.waiting_for}<span class="muted"> needs {first.waiting_for}</span>{/if}
      </a>
      <div class="cwd muted small">{shortCwd(first.cwd)}</div>
    </div>
  </div>
{/if}

<style>
  .strip {
    display: flex;
    gap: 16px;
    align-items: flex-start;
    padding: 14px 16px;
    background: var(--signal-soft);
    border-bottom: 1px solid var(--signal);
  }
  .n { font-size: 30px; line-height: 1.15; font-weight: 600; color: var(--signal); min-width: 36px; }
  .lead { font-weight: 500; }
  .which { display: block; color: var(--ink); }
</style>
