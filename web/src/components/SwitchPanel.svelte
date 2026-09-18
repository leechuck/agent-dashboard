<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { fleet } from '../lib/store.svelte'
  import AgentPicker from './AgentPicker.svelte'
  import type { AgentChoice, Session } from '../lib/types'

  let { session, onclose }: { session: Session; onclose: () => void } = $props()
  const x = $derived((session.extra ?? {}) as Record<string, any>)
  // a bare tmux pane is the agent running in it (often one still at a trust or login question)
  const harness = $derived(session.harness === 'tmux' ? String((session.extra as any)?.agent ?? '') : session.harness)
  // start from what the session is on now
  let agent = $state<AgentChoice>({
    harness: ['claude', 'codex', 'pi', 'opencode'].includes(harness) ? harness : 'claude',
    backend: session.harness === 'claude' && session.extra?.config_dir ? 'login' : 'default',
    login: String(session.extra?.config_dir ?? '').split('/').at(-1) ?? '', endpoint: '', model: '', effort: '', permissions: 'default',
  })
  let note = $state('')
  let stopOld = $state(false)
  let force = $state(false)
  let busy = $state(false)
  let result = $state<{ ok: boolean; text: string; terminal?: string } | null>(null)
  const same = $derived(agent.harness === harness)
  const resumable = $derived(['claude', 'codex', 'pi'].includes(harness))

  let catalogLoading = $state(false)
  let catalogError = $state('')
  const cat = $derived(fleet.catalog?.machines[session.machine])
  const alternateLogins = $derived((cat?.logins ?? []).filter((l, i, ls) => l.logged_in && l.account !== x.account && ls.findIndex(other => other.account === l.account) === i))
  const codexAvailable = $derived(cat?.harnesses?.codex ?? fleet.machines[session.machine]?.harnesses.includes('codex'))
  async function loadChoices() {
    catalogLoading = true; catalogError = ''
    try {
      const c = await api.catalog(true, session.machine, true)
      const local = c.machines[session.machine]
      if (!local?.ok) throw new Error(local?.error ?? 'Could not load available subscriptions.')
      const previous = fleet.catalog?.machines[session.machine]
      fleet.catalog = { endpoints: c.endpoints, machines: { ...fleet.catalog?.machines,
        [session.machine]: { ...local, models: {...previous?.models, ...local.models}, endpoints: previous?.endpoints ?? local.endpoints } } }
    } catch(e) { catalogError = String(e) }
    finally { catalogLoading = false }
  }
  function useLogin(dir: string) { agent = { ...agent, harness:'claude', backend:'login', login:dir, endpoint:'', model:'', effort:'' } }
  function useCodex() { agent = { ...agent, harness:'codex', backend:'default', login:'', endpoint:'', model:'', effort:'' } }
  onMount(() => { void loadChoices() })

  async function go() {
    busy = true
    result = null
    try {
      const r = await api.switchSession(session.key, { ...agent, note, force, stop_old: stopOld })
      result = r.ok
        ? { ok: true, terminal: r.terminal_key, text: r.resumed ? `The conversation continues with the new settings in tmux (${r.attach}).` : `A ${agent.harness} agent took over in the same folder (${r.attach}).` }
        : { ok: false, text: r.error ?? 'failed' }
    } catch (e) {
      result = { ok: false, text: String(e) }
    } finally {
      busy = false
    }
  }
</script>

<div class="sw">
  <div class="lead">Switch agent, model or subscription</div>
  <p class="small muted">
    {#if same}Same agent: the conversation is kept. The current process is ended and restarted with <code>--resume</code> on what you pick{session.harness === 'claude' ? ' (another Claude login works because logins share transcripts)' : ''}.
    {:else}Another agent cannot load this conversation. It starts in the same folder with a briefing and the path of this transcript.{/if}
  </p>
  <div class="shortcuts">
    {#each alternateLogins as login}<button disabled={busy} onclick={() => useLogin(login.dir)}>Use {login.name} · {login.account}</button>{/each}
    {#if harness !== 'codex' && codexAvailable}<button disabled={busy} onclick={useCodex}>Use Codex</button>{/if}
    <button disabled={busy || catalogLoading} onclick={loadChoices}>{catalogLoading ? 'Reading subscriptions…' : 'Refresh choices'}</button>
  </div>
  {#if catalogError}<p class="err" role="alert">{catalogError}</p>{/if}
  <AgentPicker machine={session.machine} bind:value={agent} />
  <textarea rows="2" bind:value={note} placeholder={same ? 'Optional first message after the restart' : 'What should the new agent do? (optional: default is "continue that work")'}></textarea>
  <div class="opts small">
    {#if same}
      {#if session.status === 'busy'}<label><input type="checkbox" bind:checked={force} /> it is working now: interrupt it</label>{/if}
    {:else}
      <label><input type="checkbox" bind:checked={stopOld} /> end the current {session.harness} session</label>
    {/if}
  </div>
  <div class="acts">
    <button class="primary" disabled={busy || (same && !resumable) || (same && session.status === 'busy' && !force)} onclick={go}>{busy ? 'Switching…' : same ? 'Continue with selected subscription / model' : 'Continue in ' + agent.harness}</button>
    <button onclick={onclose}>Close</button>
    {#if same && !resumable}<span class="small muted">{session.harness} sessions cannot be restarted from here.</span>{/if}
  </div>
  {#if result}
    <p class={`small ${result.ok ? 'okmsg' : 'err'}`}>{result.text}
      {#if result.terminal}<a href={`#/terminal/${encodeURIComponent(result.terminal)}?control=1`}>Open its terminal</a>{/if}
    </p>
  {/if}
  <p class="small muted now">Now: {session.harness}{x.account ? ` · ${x.account}` : ''}{session.model ? ` · ${session.model}` : ''}{x.effort ? ` · think ${x.effort}` : ''}</p>
</div>

<style>
  .sw { margin-top: 8px; padding: 12px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); display: grid; gap: 8px; }
  .shortcuts { display: flex; flex-wrap: wrap; gap: 8px; }
  .lead { font-weight: 600; }
  p { margin: 0; }
  textarea { width: 100%; padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font: inherit; font-size: 14px; }
  .opts label { display: inline-flex; gap: 6px; align-items: center; }
  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .okmsg { color: var(--moss); }
  .err { color: var(--signal); }
  .now { border-top: 1px solid var(--hairline); padding-top: 6px; }
</style>
