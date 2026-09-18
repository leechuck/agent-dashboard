<script lang="ts">
  import AgendaPanel from './AgendaPanel.svelte'
  import PersonalBriefing from './PersonalBriefing.svelte'
  import TodoPanel from './TodoPanel.svelte'
  import WeeklyReports from './WeeklyReports.svelte'
  let { wide = false, tab = 'briefing' }: { wide?: boolean; tab?: string } = $props()
  const tabs = [['briefing', 'Briefing'], ['weekly', 'Weekly reports'], ['tasks', 'Tasks'], ['calendar', 'Calendar']]
</script>
<nav aria-label="Personal views">
  {#each tabs as [id, label]}
    <a href={`#/personal/${id}`} aria-current={tab === id ? 'page' : undefined}>{label}</a>
  {/each}
</nav>
{#if tab === 'weekly'}<WeeklyReports />
{:else if tab === 'tasks'}<TodoPanel />
{:else if tab === 'calendar'}<AgendaPanel />
{:else}<PersonalBriefing {wide} />{/if}
<style>
  nav { display: flex; gap: 18px; padding: 14px 16px; border-bottom: 1px solid var(--hairline); overflow-x: auto; white-space: nowrap; }
  a { color: var(--muted); font-size: 14px; }
  a[aria-current] { color: var(--cobalt); font-weight: 600; }
</style>
