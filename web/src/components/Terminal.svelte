<script lang="ts">
  import { onMount } from 'svelte'
  import { Terminal } from '@xterm/xterm'
  import { FitAddon } from '@xterm/addon-fit'
  import '@xterm/xterm/css/xterm.css'
  import { fleet } from '../lib/store.svelte'
  import LoginHelper from './LoginHelper.svelte'

  let { key, control: startControl = false }: { key: string; control?: boolean } = $props()
  const session = $derived(fleet.sessions[key])
  let host: HTMLDivElement | undefined = $state()
  // opened to be used (a login): typing works at once; otherwise it only watches
  let control = $state(startControl)
  function toggle() {
    control = !control
    if (control) term?.focus() // the button took the focus; give it back to the terminal
  }
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
    if (control) term.focus()
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
  <button class:primary={control} onclick={toggle}>{control ? 'Typing goes to the terminal' : 'Take control'}</button>
</div>
<LoginHelper paneKey={key} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="term" bind:this={host} onclick={() => term?.focus()}></div>
<p class="small muted hint">tmux {(session?.extra as any)?.tmux?.target ?? key.split(':').slice(3).join(':')} on {session?.machine ?? key.split(':')[0]}. {control ? 'Click into the terminal and type.' : 'Watching only: "Take control" to type.'} To select text for copying, hold Shift while dragging.</p>

<style>
  .thead { display: flex; gap: 12px; align-items: center; padding: 8px 16px; border-bottom: 1px solid var(--hairline); background: var(--surface); position: sticky; top: var(--sticky-top, 48px); z-index: 3; }
  .name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .term { height: calc(100vh - 48px - 46px - 40px - 56px); min-height: 280px; background: #141a21; padding: 4px; }
  .hint { padding: 6px 16px; margin: 0; }
  @media (min-width: 960px) { .thead { top: 0; } }
</style>
