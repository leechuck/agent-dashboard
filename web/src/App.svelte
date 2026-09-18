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
  // the terminal brings xterm.js (a third of the bundle): fetched only when one is opened
  const terminalView = () => import('./components/Terminal.svelte')
  import Overview from './components/Overview.svelte'
  import Personal from './components/Personal.svelte'
  import Group from './components/Group.svelte'

  let route = $state(parse(location.hash))

  function parse(hash: string): { page: string; key?: string; q?: URLSearchParams; tab?: string } {
    const h = hash.replace(/^#\/?/, '')
    if (h.startsWith('new')) return { page: 'new', q: new URLSearchParams(h.split('?')[1] ?? '') }
    if (h === 'login') return { page: 'login' }
    if (h === 'settings') return { page: 'settings' }
    if (h === 'group') return { page: 'group' }
    if (h === 'limits') return { page: 'limits' }
    if (h === 'overview' || h === 'cockpit') return { page: 'overview' }
    if (h === 'personal' || h.startsWith('personal/')) return { page: 'personal', tab: h.split('/')[1] || 'briefing' }
    if (h.startsWith('decisions')) return { page: 'decisions' }
    if (h.startsWith('history/')) return { page: 'history', key: decodeURIComponent(h.slice(8)) }
    if (h === 'history') return { page: 'history' }
    if (h.startsWith('terminal/')) {
      const [path, query] = h.slice(9).split('?')
      return { page: 'terminal', key: decodeURIComponent(path), q: new URLSearchParams(query ?? '') }
    }
    if (h.startsWith('session/')) {
      const [path, query] = h.slice(8).split('?')
      const key = decodeURIComponent(path)
      const draft = new URLSearchParams(query ?? '').get('draft')
      if (draft) fleet.setDraft(key, draft)
      return { page: 'session', key }
    }
    return { page: 'fleet' }
  }

  onMount(() => {
    const onHash = () => (route = parse(location.hash))
    window.addEventListener('hashchange', onHash)
    fleet.load()
    return () => window.removeEventListener('hashchange', onHash)
  })

  let wide = $state(false)
  const home = $derived(route.page === 'fleet')
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
      <aside class:home><Fleet selected={route.key} layout={home ? 'board' : 'rail'} cockpitCard={home} /></aside>
      <section class:home>
        {#if route.page === 'decisions'}
          <Decisions />
        {:else if route.page === 'settings'}
          <Settings />
        {:else if route.page === 'history'}
          <History id={route.key} />
        {:else if route.page === 'group'}
          <Group />
        {:else if route.page === 'limits'}
          <Limits />
        {:else if route.page === 'terminal' && route.key}
          {#key route.key}{#await terminalView() then T}<T.default key={route.key} control={route.q?.get('control') === '1'} />{/await}{/key}
        {:else if route.page === 'new'}
          <NewSession machine={route.q?.get('machine') ?? ''} resume={route.q?.get('resume') ?? ''} title={route.q?.get('cwd') ?? ''} />
        {:else if route.key}
          <SessionView key={route.key} />
        {:else if route.page === 'personal'}
          <Personal wide tab={route.tab} />
        {:else if route.page === 'overview'}
          <Overview />
        {/if}
      </section>
    {:else if route.page === 'decisions'}
      <Decisions />
    {:else if route.page === 'settings'}
      <Settings />
    {:else if route.page === 'history'}
      <History id={route.key} />
    {:else if route.page === 'group'}
      <Group />
    {:else if route.page === 'limits'}
      <Limits />
    {:else if route.page === 'terminal' && route.key}
      {#key route.key}{#await terminalView() then T}<T.default key={route.key} control={route.q?.get('control') === '1'} />{/await}{/key}
    {:else if route.page === 'new'}
      <NewSession machine={route.q?.get('machine') ?? ''} resume={route.q?.get('resume') ?? ''} title={route.q?.get('cwd') ?? ''} />
    {:else if route.page === 'session' && route.key}
      <SessionView key={route.key} />
    {:else if route.page === 'overview'}
      <Overview />
    {:else if route.page === 'personal'}
      <Personal tab={route.tab} />
    {:else}
      <Fleet selected={undefined} cockpitCard layout="board" />
    {/if}
  </main>
{/if}

<style>
  main { max-width: 720px; margin: 0 auto; padding: 0 0 48px; }
  main.wide { max-width: none; margin: 0; padding: 0; display: flex; height: calc(100vh - 48px); overflow: hidden; }
  /* home: the board fills the page. Anything else: the board folds into a rail and the page slides in. */
  main.wide aside { flex: 0 0 340px; border-right: 1px solid var(--hairline); overflow-y: auto; --sticky-top: 0px; transition: flex-basis .28s cubic-bezier(.2, .8, .2, 1); }
  main.wide aside.home { flex-basis: 100%; border-right: 0; }
  main.wide section { flex: 1 1 0; overflow-y: auto; min-width: 0; --sticky-top: 0px; animation: slidein .28s cubic-bezier(.2, .8, .2, 1); }
  main.wide section.home { display: none; }
  @keyframes slidein { from { transform: translateX(32px); opacity: 0; } to { transform: none; opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { main.wide aside { transition: none; } main.wide section { animation: none; } }
</style>
