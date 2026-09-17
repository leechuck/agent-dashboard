<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import AgentPicker from './AgentPicker.svelte'
  import type { AgentChoice, Session } from '../lib/types'

  // Resume this conversation on another machine: its transcript travels through the hub,
  // the agent starts there in tmux with --resume. A copy of the transcript stays here.
  let { session, target = '', onclose }: { session: Session; target?: string; onclose: () => void } = $props()
  const live = $derived(['busy', 'idle', 'waiting'].includes(session.status) && !!session.pid)
  const others = $derived(Object.values(fleet.machines).filter((m) => m.online && m.id !== session.machine).map((m) => m.id))
  let machine = $state(target)
  let cwd = $state(session.cwd)
  let dirs = $state<string[]>([])
  let agent = $state<AgentChoice>({ harness: session.harness, backend: 'default', login: '', endpoint: '', model: '', effort: '', permissions: 'default' })
  let note = $state('')
  let stopOld = $state(true)
  let force = $state(false)
  let busy = $state(false)
  let result = $state<{ ok: boolean; text: string; terminal?: string; key?: string } | null>(null)

  onMount(() => fleet.loadCatalog())
  $effect(() => {
    if (!machine && others.length) machine = others[0]
  })
  $effect(() => {
    if (!machine) return
    api.dirs(machine).then((d) => (dirs = d)).catch(() => (dirs = []))
  })

  async function go() {
    busy = true
    result = null
    try {
      const r = await api.moveSession(session.key, { ...agent, machine, cwd, note, stop_old: stopOld, force })
      result = r.ok
        ? { ok: true, terminal: r.terminal_key, key: r.session_key, text: `It continues on ${machine} in tmux (${r.attach}).${r.stopped ? ` The process on ${session.machine} was ended; its transcript stays there too.` : ''}` }
        : { ok: false, text: r.error ?? 'failed' }
    } catch (e) {
      result = { ok: false, text: String(e) }
    } finally {
      busy = false
    }
  }
</script>

<div class="mv">
  <div class="lead">Move to another machine</div>
  <p class="small muted">
    The transcript is copied over and the same {session.harness} conversation resumes there. The folder must exist on the other machine (clone the repository first); the copy here stays, so nothing is lost.
  </p>
  {#if others.length === 0}
    <p class="small err">No other machine is online.</p>
  {:else}
    <div class="row">
      <label>To
        <select bind:value={machine}>{#each others as m}<option value={m}>{m}</option>{/each}</select>
      </label>
      <label>Folder there
        <input list="movedirs" bind:value={cwd} placeholder={session.cwd} />
        <datalist id="movedirs">{#each dirs as d}<option value={d}></option>{/each}</datalist>
      </label>
    </div>
    <AgentPicker {machine} bind:value={agent} lockHarness />
    <textarea rows="2" bind:value={note} placeholder="Optional first message after it resumes there"></textarea>
    <div class="opts small">
      {#if live}
        <label><input type="checkbox" bind:checked={stopOld} /> end it on {session.machine} first (two agents must not write one transcript)</label>
        {#if session.status === 'busy'}<label><input type="checkbox" bind:checked={force} /> it is working now: interrupt it</label>{/if}
      {/if}
    </div>
    <div class="acts">
      <button class="primary" disabled={busy || !machine || !cwd || (live && session.status === 'busy' && !force)} onclick={go}>{busy ? 'Moving…' : `Move to ${machine}`}</button>
      <button onclick={onclose}>Close</button>
    </div>
  {/if}
  {#if result}
    <p class={`small ${result.ok ? 'okmsg' : 'err'}`}>{result.text}
      {#if result.key}<a href={`#/session/${encodeURIComponent(result.key)}`}>Open it</a>{/if}
      {#if result.terminal}<a href={`#/terminal/${encodeURIComponent(result.terminal)}?control=1`}>Terminal</a>{/if}
    </p>
  {/if}
</div>

<style>
  .mv { margin-top: 8px; padding: 12px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); display: grid; gap: 8px; }
  .lead { font-weight: 600; }
  p { margin: 0; }
  .row { display: grid; grid-template-columns: minmax(120px, 1fr) 2fr; gap: 10px; }
  label { display: grid; gap: 4px; font-size: 13px; }
  input, select, textarea { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font: inherit; font-size: 14px; }
  textarea { width: 100%; }
  .opts { display: grid; gap: 4px; }
  .opts label { display: inline-flex; gap: 6px; align-items: center; }
  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .okmsg { color: var(--moss); }
  .err { color: var(--signal); }
  .okmsg a, .err a { margin-left: 8px; }
</style>
