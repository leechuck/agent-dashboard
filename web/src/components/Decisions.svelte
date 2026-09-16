<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import DecisionCard from './DecisionCard.svelte'
  const pending = $derived(fleet.pendingDecisions)
  const recent = $derived(fleet.recentDecisions)
  const armedAny = $derived(Object.values(fleet.machines).some((m) => m.armed && (m.armed_until === 0 || m.armed_until > Date.now())))
</script>

<section>
  <h1>Decisions</h1>
  {#if pending.length === 0}
    <p class="notice muted">
      Nothing is waiting for you.
      {#if !armedAny}
        Approvals reach your phone only while a machine is armed. Arm one from its header on the fleet page.
      {/if}
    </p>
  {/if}
  {#each pending as d (d.id)}
    <DecisionCard {d} />
  {/each}
  {#if recent.length > 0}
    <h2 class="muted">Earlier</h2>
    {#each recent as d (d.id)}
      <DecisionCard {d} compact />
    {/each}
  {/if}
</section>

<style>
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  h2 { font-size: 15px; font-weight: 500; margin: 20px 16px 6px; }
  .notice { padding: 0 16px 12px; margin: 0; }
</style>
