<script lang="ts">
  import { onMount } from 'svelte'
  import { Fleet, fleet } from '../lib/store.svelte'
  import { displayName } from '../lib/format'

  let { current }: { current: string } = $props()
  const list = $derived(
    fleet.sessionList.filter(
      (s) => s.key === current || (['busy', 'idle', 'waiting'].includes(s.status) && !Fleet.isStale(s) && !(s.extra as any)?.parent && s.harness !== 'tmux'),
    ),
  )
  const index = $derived(list.findIndex((s) => s.key === current))
  let bar: HTMLElement | undefined = $state()

  function go(step: number) {
    if (!list.length) return
    const next = list[(index + step + list.length) % list.length]
    if (next) location.hash = `#/session/${encodeURIComponent(next.key)}`
  }
  $effect(() => {
    current
    queueMicrotask(() => bar?.querySelector('.on')?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' }))
  })
  onMount(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === ']' || e.key === 'ArrowRight') go(1)
      else if (e.key === '[' || e.key === 'ArrowLeft') go(-1)
      else if (e.key === 'Escape') location.hash = '#/'
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
</script>

<nav class="sw" bind:this={bar} aria-label="Sessions">
  <a class="all" href="#/" title="All agents (Esc)">▦</a>
  {#each list as s (s.key)}
    <a class={`chip ${s.status}`} class:on={s.key === current} href={`#/session/${encodeURIComponent(s.key)}`} title={`${s.machine} · ${s.harness}`}>
      <span class={`dot ${s.status}`}></span>{displayName(s)}
    </a>
  {/each}
  <span class="hint">← → switch · Esc all</span>
</nav>

<style>
  .sw { display: flex; gap: 6px; align-items: center; padding: 7px 12px; overflow-x: auto; scrollbar-width: none; background: var(--page); border-bottom: 1px solid var(--hairline); white-space: nowrap; }
  .all { flex: none; padding: 2px 8px; font-size: 16px; color: var(--muted); text-decoration: none; }
  .chip { flex: none; display: inline-flex; align-items: center; gap: 6px; max-width: 220px; padding: 3px 10px; border: 1px solid var(--hairline); border-radius: 14px; background: var(--surface); color: var(--muted); font-size: 13px; text-decoration: none; overflow: hidden; text-overflow: ellipsis; transition: background .15s, color .15s; }
  .chip:hover { color: var(--ink); text-decoration: none; }
  .chip.on { background: var(--ink); border-color: var(--ink); color: var(--page); }
  .chip.waiting:not(.on) { border-color: var(--signal); color: var(--signal); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--hairline); flex: none; }
  .dot.busy { background: var(--cobalt); }
  .dot.waiting { background: var(--signal); }
  .dot.idle { background: var(--amber); }
  .hint { flex: none; margin-left: auto; padding-left: 12px; font-size: 11.5px; color: var(--muted); }
  @media (max-width: 700px) { .hint { display: none; } }
</style>
