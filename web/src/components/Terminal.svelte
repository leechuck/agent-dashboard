<script lang="ts">
  import { onMount } from 'svelte'
  import { Terminal } from '@xterm/xterm'
  import { FitAddon } from '@xterm/addon-fit'
  import '@xterm/xterm/css/xterm.css'
  import { fleet } from '../lib/store.svelte'

  let { key }: { key: string } = $props()
  const session = $derived(fleet.sessions[key])
  let host: HTMLDivElement | undefined = $state()
  let control = $state(false)
  let status = $state('connecting')
  let ws: WebSocket | null = null
  let term: Terminal | null = null

  onMount(() => {
    term = new Terminal({
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 13,
      theme: { background: '#141a21', foreground: '#e7ecf1' },
      cursorBlink: true,
      scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host!)
    fit.fit()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/terminal/${encodeURIComponent(key)}?cols=${term.cols}&rows=${term.rows}`)
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => (status = 'live')
    ws.onclose = () => (status = 'closed')
    ws.onerror = () => (status = 'error')
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) term!.write(new Uint8Array(ev.data))
      else {
        try {
          const m = JSON.parse(ev.data)
          if (m.type === 'error') status = m.message
        } catch {
          term!.write(ev.data)
        }
      }
    }
    term.onData((d) => {
      if (control && ws?.readyState === 1) ws.send(new TextEncoder().encode(d))
    })
    const ro = new ResizeObserver(() => {
      fit.fit()
      if (ws?.readyState === 1) ws.send(JSON.stringify({ type: 'resize', cols: term!.cols, rows: term!.rows }))
    })
    ro.observe(host!)
    return () => {
      ro.disconnect()
      ws?.close()
      term?.dispose()
    }
  })
</script>

<div class="thead">
  <a href={`#/session/${encodeURIComponent(key)}`}>← Session</a>
  <span class="name">{session?.name ?? key}</span>
  <span class="small muted">{status}</span>
  <button class:primary={control} onclick={() => (control = !control)}>{control ? 'Controlling' : 'Take control'}</button>
</div>
<div class="term" bind:this={host}></div>
<p class="small muted hint">Attached to tmux {(session?.extra as any)?.tmux?.target} on {session?.machine}. Keystrokes only go through while "Controlling" is on. Ctrl+B D detaches.</p>

<style>
  .thead { display: flex; gap: 12px; align-items: center; padding: 8px 16px; border-bottom: 1px solid var(--hairline); background: var(--surface); position: sticky; top: 48px; z-index: 3; }
  .name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .term { height: calc(100vh - 48px - 46px - 40px); background: #141a21; padding: 4px; }
  .hint { padding: 6px 16px; margin: 0; }
  @media (min-width: 960px) { .thead { top: 0; } }
</style>
