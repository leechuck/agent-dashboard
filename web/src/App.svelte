<script lang="ts">
  import { onMount } from 'svelte'
  import { fleet } from './lib/store.svelte'
  import Fleet from './components/Fleet.svelte'
  import SessionView from './components/SessionView.svelte'
  import Login from './components/Login.svelte'
  import TopBar from './components/TopBar.svelte'
  import Decisions from './components/Decisions.svelte'
  import Settings from './components/Settings.svelte'
  import History from './components/History.svelte'
  import NewSession from './components/NewSession.svelte'
  import Limits from './components/Limits.svelte'

  let route = $state(parse(location.hash))

  function parse(hash: string): { page: string; key?: string; q?: URLSearchParams } {
    const h = hash.replace(/^#\/?/, '')
    if (h.startsWith('new')) return { page: 'new', q: new URLSearchParams(h.split('?')[1] ?? '') }
    if (h === 'login') return { page: 'login' }
    if (h === 'settings') return { page: 'settings' }
    if (h === 'limits') return { page: 'limits' }
    if (h.startsWith('decisions')) return { page: 'decisions' }
    if (h.startsWith('history/')) return { page: 'history', key: decodeURIComponent(h.slice(8)) }
    if (h === 'history') return { page: 'history' }
    if (h.startsWith('session/')) return { page: 'session', key: decodeURIComponent(h.slice(8)) }
    return { page: 'fleet' }
  }

  onMount(() => {
    const t = localStorage.getItem('theme')
    if (t && t !== 'auto') document.documentElement.dataset.theme = t
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
        {#if route.page === 'decisions'}
          <Decisions />
        {:else if route.page === 'settings'}
          <Settings />
        {:else if route.page === 'history'}
          <History id={route.key} />
        {:else if route.page === 'limits'}
          <Limits />
        {:else if route.page === 'new'}
          <NewSession machine={route.q?.get('machine') ?? ''} resume={route.q?.get('resume') ?? ''} title={route.q?.get('cwd') ?? ''} />
        {:else if route.key}
          <SessionView key={route.key} />
        {:else}
          <div class="placeholder muted">Pick a session to read along.</div>
        {/if}
      </section>
    {:else if route.page === 'decisions'}
      <Decisions />
    {:else if route.page === 'settings'}
      <Settings />
    {:else if route.page === 'history'}
      <History id={route.key} />
    {:else if route.page === 'limits'}
      <Limits />
    {:else if route.page === 'new'}
      <NewSession machine={route.q?.get('machine') ?? ''} resume={route.q?.get('resume') ?? ''} title={route.q?.get('cwd') ?? ''} />
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
