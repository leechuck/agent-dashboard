<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  const pending = $derived(fleet.pendingDecisions.length)
  const waiting = $derived(fleet.waiting.length)
  const worst = $derived(fleet.worstUsage)
  const needs = $derived(fleet.cockpit?.findings.filter((f) => f.severity === 'act').length ?? 0)
</script>

<header>
  <a class="brand" href="#/">agentdash</a>
  <span class="link" class:live={fleet.connected} title={fleet.connected ? 'live updates on' : 'reconnecting'}></span>
  <nav>
    <a href="#/cockpit" class:hot={needs > 0}>Cockpit{needs > 0 ? ` ${needs}` : ''}</a>
    <a href="#/decisions" class:hot={pending > 0}>Decisions{pending > 0 ? ` ${pending}` : ''}</a>
    <a href="#/limits" class:warn={!!worst && worst.used_pct >= 80} title={worst ? `${worst.provider} ${worst.label}` : ''}>Limits{worst ? ` ${worst.used_pct.toFixed(0)}%` : ''}</a>
    <a href="#/history">History</a>
    <a href="#/settings">Settings</a>
  </nav>
  {#if pending === 0 && waiting > 0}
    <span class="count">{waiting} waiting</span>
  {/if}
  <span class="sr" aria-hidden="true"></span>
</header>

<style>
  header {
    height: 48px;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    white-space: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
    border-bottom: 1px solid var(--hairline);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 5;
  }
  .brand { font-weight: 600; color: var(--ink); letter-spacing: -0.01em; }
  .link { width: 8px; height: 8px; border-radius: 50%; background: var(--hairline); }
  .link.live { background: var(--moss); }
  nav { margin-left: auto; display: flex; gap: 14px; font-size: 14px; flex: none; }
  .sr { display: none; }
  @media (max-width: 480px) {
    header { gap: 10px; padding: 0 12px; }
    .brand { font-size: 14px; }
    nav { gap: 10px; font-size: 13px; }
    .count { display: none; }
  }
  nav a { color: var(--muted); }
  nav a.hot { color: var(--signal); font-weight: 600; }
  nav a.warn { color: var(--amber); font-weight: 600; }
  .count { color: var(--signal); font-size: 13px; }
</style>
