<script lang="ts">
  import { onMount } from 'svelte'
  import { fleet } from './lib/store.svelte'
  import Fleet from './components/Fleet.svelte'
  import SessionView from './components/SessionView.svelte'
  import Login from './components/Login.svelte'
  import TopBar from './components/TopBar.svelte'

  let route = $state(parse(location.hash))

  function parse(hash: string): { page: string; key?: string } {
    const h = hash.replace(/^#\/?/, '')
    if (h === 'login') return { page: 'login' }
    if (h.startsWith('session/')) return { page: 'session', key: decodeURIComponent(h.slice(8)) }
    return { page: 'fleet' }
  }

  onMount(() => {
    const onHash = () => (route = parse(location.hash))
    window.addEventListener('hashchange', onHash)
    fleet.load()
    return () => window.removeEventListener('hashchange', onHash)
  })

  let wide = $state(false)
  onMount(() => {
    const mq = window.matchMedia('(min-width: 960px)')
    const set = () => (wide = mq.matches)
    set()
    mq.addEventListener('change', set)
    return () => mq.removeEventListener('change', set)
  })
</script>

{#if route.page === 'login'}
  <Login />
{:else}
  <TopBar />
  <main class:wide>
    {#if wide}
      <aside><Fleet selected={route.key} /></aside>
      <section>
        {#if route.key}
          <SessionView key={route.key} />
        {:else}
          <div class="placeholder muted">Pick a session to read along.</div>
        {/if}
      </section>
    {:else if route.page === 'session' && route.key}
      <SessionView key={route.key} />
    {:else}
      <Fleet selected={undefined} />
    {/if}
  </main>
{/if}

<style>
  main { max-width: 720px; margin: 0 auto; padding: 0 0 48px; }
  main.wide {
    max-width: none;
    margin: 0;
    display: grid;
    grid-template-columns: 380px 1fr;
    height: calc(100vh - 48px);
  }
  main.wide aside { border-right: 1px solid var(--hairline); overflow-y: auto; }
  main.wide section { overflow-y: auto; min-width: 0; }
  .placeholder { padding: 48px 24px; }
</style>
