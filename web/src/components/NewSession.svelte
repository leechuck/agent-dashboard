<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'

  let { machine: initialMachine = '', resume = '', title = '' }: { machine?: string; resume?: string; title?: string } = $props()
  let machine = $state(initialMachine)
  let cwd = $state(title)
  let prompt = $state('')
  let name = $state('')
  let mode = $state('default')
  let dirs = $state<string[]>([])
  let busy = $state(false)
  let msg = $state('')
  let ok = $state(false)

  const machines = $derived(Object.values(fleet.machines).filter((m) => m.online).map((m) => m.id))

  async function loadDirs() {
    if (!machine) return
    try {
      dirs = await api.dirs(machine)
      if (!cwd && dirs.length) cwd = dirs[0]
    } catch {
      dirs = []
    }
  }
  onMount(() => {
    if (!machine && machines.length) machine = machines[0]
    loadDirs()
  })
  $effect(() => {
    machine
    loadDirs()
  })

  async function start(e: Event) {
    e.preventDefault()
    busy = true
    msg = ''
    try {
      const r = await api.startSession(machine, { cwd, prompt, name, resume, permission_mode: mode })
      ok = r.ok
      msg = r.ok ? `Started${r.job_id ? ` as ${r.job_id}` : ''}. It appears in the fleet in a few seconds.` : r.error || r.output || 'failed'
      if (r.ok) prompt = ''
    } catch (err) {
      ok = false
      msg = String(err)
    } finally {
      busy = false
    }
  }
</script>

<form class="new" onsubmit={start}>
  <h1>{resume ? 'Resume in the background' : 'New background session'}</h1>
  <label>Machine
    <select bind:value={machine}>
      {#each machines as m}<option value={m}>{m}</option>{/each}
    </select>
  </label>
  <label>Directory
    <input list="dirs" bind:value={cwd} placeholder="/home/leechuck/…" required />
    <datalist id="dirs">{#each dirs as d}<option value={d}></option>{/each}</datalist>
  </label>
  <label>{resume ? 'Message (optional)' : 'Task'}
    <textarea rows="4" bind:value={prompt} required={!resume} placeholder={resume ? 'Continue with…' : 'What should it do?'}></textarea>
  </label>
  <div class="row">
    <label>Name <input bind:value={name} placeholder="optional" /></label>
    <label>Permissions
      <select bind:value={mode}>
        <option value="default">ask (default)</option>
        <option value="acceptEdits">accept edits</option>
        <option value="plan">plan only</option>
      </select>
    </label>
  </div>
  <p class="small muted">Runs <code>claude --bg</code> on that machine as a Claude Code background session. Arm the machine to get its permission prompts here.</p>
  <div class="actions">
    <button class="primary" type="submit" disabled={busy || !machine || !cwd}>{busy ? 'Starting…' : 'Start'}</button>
    <a href="#/">Cancel</a>
  </div>
  {#if msg}<p class:ok class:err={!ok}>{msg}</p>{/if}
</form>

<style>
  .new { padding: 16px; display: grid; gap: 12px; max-width: 640px; }
  h1 { font-size: 22px; margin: 0; }
  label { display: grid; gap: 4px; font-size: 14px; }
  input, select, textarea { padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font-size: 15px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .actions { display: flex; gap: 14px; align-items: center; }
  .ok { color: var(--moss); }
  .err { color: var(--signal); }
</style>
