<script lang="ts">
  import type { Session } from '../lib/types'
  import { api } from '../lib/api'
  let { session }: { session: Session } = $props()
  let busy = $state('')
  let msg = $state('')
  let confirmEnd = $state(false)

  const isBg = $derived(session.kind === 'background')
  const live = $derived(['busy', 'idle', 'waiting'].includes(session.status))

  async function run(action: string) {
    busy = action
    msg = ''
    try {
      const r = await api.action(session.key, action)
      msg = r.ok ? `${action}: done` : `${action}: ${r.error ?? r.output ?? 'failed'}`
    } catch (e) {
      msg = String(e)
    } finally {
      busy = ''
      confirmEnd = false
    }
  }
</script>

<div class="acts">
  {#if isBg}
    {#if live}<button disabled={!!busy} onclick={() => run('stop')}>Stop</button>{/if}
    {#if !live}<button disabled={!!busy} onclick={() => run('respawn')}>Respawn</button>{/if}
    <button disabled={!!busy} onclick={() => run('rm')}>Remove from list</button>
  {:else if session.pid && live}
    {#if confirmEnd}
      <span class="small">End the terminal session?</span>
      <button class="danger" disabled={!!busy} onclick={() => run('terminate')}>Yes, end it</button>
      <button disabled={!!busy} onclick={() => (confirmEnd = false)}>No</button>
    {:else}
      <button disabled={!!busy} onclick={() => (confirmEnd = true)}>End session</button>
    {/if}
  {/if}
  {#if session.session_id}
    <a class="btn" href={`#/new?resume=${encodeURIComponent(session.session_id)}&machine=${encodeURIComponent(session.machine)}&cwd=${encodeURIComponent(session.cwd)}`}>Fork to background</a>
  {/if}
  {#if msg}<span class="small muted">{msg}</span>{/if}
</div>

<style>
  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
  .acts button { font-size: 13px; padding: 4px 10px; }
  .btn { font-size: 13px; padding: 4px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); color: var(--ink); }
</style>
