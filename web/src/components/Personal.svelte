<script lang="ts">
  import AgendaPanel from './AgendaPanel.svelte'
  import PersonalBriefing from './PersonalBriefing.svelte'
  import TodoPanel from './TodoPanel.svelte'

  let { wide = false }: { wide?: boolean } = $props()
</script>

<div class="personal" class:wide>
  <div class="col">
    <AgendaPanel />
    <TodoPanel />
  </div>
  <div class="col">
    <PersonalBriefing {wide} />
  </div>
</div>

<style>
  .personal { display: grid; gap: 0 8px; padding-bottom: 32px; }
  .col { min-width: 0; }
  @media (min-width: 1500px) {
    .personal.wide { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
  }
</style>
