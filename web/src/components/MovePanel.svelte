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
  let cwd = $state(`${session.cwd}-moved-${session.session_id.slice(0, 8)}`)
  let dirs = $state<string[]>([])
  let agent = $state<AgentChoice>({ harness: session.harness, backend: 'default', login: '', endpoint: '', model: '', effort: '', permissions: 'default' })
  let note = $state('')
  let stopOld = $state(true)
  let createDir = $state(true)
  let force = $state(false)
  let busy = $state(false)
  const progress = $derived(fleet.moves[session.key])
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
    delete fleet.moves[session.key]
    busy = true
    result = null
    try {
      const r = await api.moveSession(session.key, { ...agent, machine, cwd, note, create_dir: createDir, stop_old: stopOld, force })
      result = r.ok
        ? { ok: true, terminal: r.terminal_key, key: r.session_key, text: `Resume started on ${machine} in tmux (${r.attach}). Open its terminal to check readiness.${r.stopped ? ` The process on ${session.machine} was ended; its transcript stays there too.` : ''}` }
        : { ok: false, text: `${r.error ?? 'failed'}${r.stopped ? ' The source was stopped; its files and conversation are still available to resume.' : ''}` }
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
    Copies the conversation, project files (including uncommitted work and local dependencies), skills and agent configuration. Uses the destination’s logins. Missing folders are created if selected below. Existing files are never overwritten; conflicting files block the move. The default is a separate project copy. Supports environments up to 32 GiB and checks available disk space. Running tools and laptop services do not migrate; the resumed agent is told to check what else it needs.
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
      <label><input type="checkbox" bind:checked={createDir} /> Create destination directory if missing</label>
      {#if live}
        <label><input type="checkbox" bind:checked={stopOld} /> end it on {session.machine} after destination checks</label>
        {#if session.status === 'busy'}<label><input type="checkbox" bind:checked={force} /> it is working now: interrupt it</label>{/if}
      {/if}
    </div>
    <div class="acts">
      <button class="primary" disabled={busy || !machine || !cwd || (live && session.status === 'busy' && !force)} onclick={go}>{busy ? 'Moving…' : `Move to ${machine}`}</button>
      <button onclick={onclose}>Close</button>
    </div>
  {/if}
  {#if busy}
    <div class="transfer-progress" role="status" aria-live="polite">
      <div>{progress ? `${progress.phase} · ${progress.stage}` : 'Preparing move…'}</div>
      <progress aria-label={progress?.stage ?? 'Preparing move'} max={progress?.total || 1} value={progress?.total ? progress.completed : undefined}></progress>
      {#if progress?.total}
        <div class="small muted">{Math.round((progress.completed ?? 0) / progress.total * 100)}% · {((progress.completed ?? 0) / 1048576).toFixed(1)} / {(progress.total / 1048576).toFixed(1)} MiB</div>
      {:else}<div class="small muted">This stage may take a while. You can keep using the dashboard.</div>{/if}
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
  .transfer-progress { display: grid; gap: 6px; overflow-wrap: anywhere; }
  progress { width: 100%; height: 12px; accent-color: var(--moss); }
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
