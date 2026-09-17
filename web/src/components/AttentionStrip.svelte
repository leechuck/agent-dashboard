<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import { displayName, shortCwd } from '../lib/format'
  import DecisionCard from './DecisionCard.svelte'
  const pending = $derived(fleet.pendingDecisions)
  const waiting = $derived(fleet.waiting)
  const first = $derived(waiting[0])
  const n = $derived(pending.length || waiting.length)
</script>

{#if pending.length > 0}
  <div class="strip">
    <div class="n">{n}</div>
    <div class="body">
      <div class="lead">{pending.length === 1 ? 'decision waiting for you' : 'decisions waiting for you'}
        {#if pending.length > 1}<a href="#/decisions">see all</a>{/if}
      </div>
    </div>
  </div>
  <DecisionCard d={pending[0]} compact />
{:else if first}
  <div class="strip">
    <div class="n">{n}</div>
    <div class="body">
      <div class="lead">{waiting.length === 1 ? 'session is waiting for you' : 'sessions are waiting for you'}</div>
      <a class="which" href={`#/session/${encodeURIComponent(first.key)}`}>
        {displayName(first)} on {first.machine}
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
  .lead a { margin-left: 8px; font-weight: 400; }
  .which { display: block; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .body { min-width: 0; flex: 1; }
</style>
