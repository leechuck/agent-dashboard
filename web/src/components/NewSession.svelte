<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import AgentPicker from './AgentPicker.svelte'
  import type { AgentChoice } from '../lib/types'

  let { machine: initialMachine = '', resume = '', title = '' }: { machine?: string; resume?: string; title?: string } = $props()
  let machine = $state(initialMachine)
  let cwd = $state(title)
  let prompt = $state('')
  let name = $state('')
  let mode = $state<'background' | 'tmux'>('background')
  let agent = $state<AgentChoice>({ harness: 'claude', backend: 'default', login: '', endpoint: '', model: '', effort: '', permissions: 'default' })
  let dirs = $state<string[]>([])
  let busy = $state(false)
  let msg = $state('')
  let ok = $state(false)

  const machines = $derived(Object.values(fleet.machines).filter((m) => m.online).map((m) => m.id))
  // only Claude has a background-job mode; everything else lives in a tmux session
  const canBackground = $derived(agent.harness === 'claude' && !!prompt.trim())
  const effectiveMode = $derived(canBackground ? mode : 'tmux')

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
    fleet.loadCatalog()
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
      const r = await api.startSession(machine, { ...agent, cwd, prompt, name, resume, mode: effectiveMode })
      ok = r.ok
      msg = r.ok
        ? r.attach
          ? `Started in tmux. It shows up on the board in a few seconds; from a terminal on ${machine}: ${r.attach}`
          : `Started${r.job_id ? ` as ${r.job_id}` : ''}. It shows up on the board in a few seconds.`
        : r.error || r.output || 'failed'
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
  <h1>{resume ? 'Resume a session' : 'New session'}</h1>
  <label>Machine
    <select bind:value={machine}>
      {#each machines as m}<option value={m}>{m}</option>{/each}
    </select>
  </label>

  <fieldset>
    <legend>Agent and model</legend>
    <AgentPicker {machine} bind:value={agent} lockHarness={!!resume} />
  </fieldset>

  <label>Directory
    <input list="dirs" bind:value={cwd} placeholder="/home/leechuck/…" required />
    <datalist id="dirs">{#each dirs as d}<option value={d}></option>{/each}</datalist>
  </label>
  <label>{resume ? 'Message (optional)' : 'Task'}
    <textarea rows="4" bind:value={prompt} placeholder={resume ? 'Continue with…' : 'What should it do? Leave empty to open it and type later.'}></textarea>
  </label>
  <div class="row">
    <label>Name <input bind:value={name} placeholder="optional" /></label>
    <label>Runs as
      <select bind:value={mode} disabled={!canBackground}>
        <option value="background">Claude background job</option>
        <option value="tmux">tmux session (terminal, /commands)</option>
      </select>
    </label>
  </div>
  <p class="small muted">
    {#if effectiveMode === 'tmux'}Runs in its own tmux session on {machine || 'the machine'}: you can type into it from here, open its terminal, or attach from a shell. A folder it has never seen may first ask whether you trust it; open the terminal to answer.
    {:else}Runs as a Claude Code background job. Arm the machine to get its permission prompts here.{/if}
  </p>
  <div class="actions">
    <button class="primary" type="submit" disabled={busy || !machine || !cwd}>{busy ? 'Starting…' : 'Start'}</button>
    <a href="#/">Cancel</a>
  </div>
  {#if msg}<p class:ok class:err={!ok}>{msg}</p>{/if}
</form>

<style>
  .new { padding: 16px; display: grid; gap: 12px; max-width: 760px; }
  h1 { font-size: 22px; margin: 0; }
  label { display: grid; gap: 4px; font-size: 14px; }
  input, select, textarea { padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font-size: 15px; }
  fieldset { border: 1px solid var(--hairline); border-radius: var(--radius); padding: 10px 12px 12px; margin: 0; background: var(--surface); }
  legend { font-size: 13px; color: var(--muted); padding: 0 6px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .actions { display: flex; gap: 14px; align-items: center; }
  .ok { color: var(--moss); }
  .err { color: var(--signal); }
</style>
