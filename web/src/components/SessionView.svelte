<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import { api } from '../lib/api'
  import { shortCwd, statusLabel } from '../lib/format'
  import MessageItem from './MessageItem.svelte'
  import Composer from './Composer.svelte'
  import SessionActions from './SessionActions.svelte'

  let { key }: { key: string } = $props()
  const session = $derived(fleet.sessions[key])
  const messages = $derived(fleet.messages[key] ?? [])
  let showThinking = $state(false)
  let showMeta = $state(false)
  let list: HTMLElement | undefined = $state()
  let stickBottom = $state(true)
  let sendState = $state<'idle' | 'sending' | 'sent' | 'failed'>('idle')
  let sendError = $state('')

  $effect(() => {
    key
    fleet.openSession(key)
  })

  $effect(() => {
    messages.length
    if (stickBottom && list) queueMicrotask(() => list && (list.scrollTop = list.scrollHeight))
  })

  function onScroll() {
    if (!list) return
    stickBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80
  }

  const visible = $derived(
    messages.filter((m) => (showThinking || m.kind !== 'thinking') && (showMeta || !m.is_meta)),
  )

  const canSend = $derived(
    !!session && session.status !== 'offline' && session.harness === 'claude' && !!(session.extra as any)?.socket,
  )

  async function send(text: string) {
    sendState = 'sending'
    sendError = ''
    try {
      const r = await api.prompt(key, text)
      sendState = r.ok ? 'sent' : 'failed'
      sendError = r.error ?? ''
    } catch (e) {
      sendState = 'failed'
      sendError = String(e)
    }
  }
</script>

{#if !session}
  <p class="notice muted">Loading session…</p>
{:else}
  <div class="head">
    <a class="back" href="#/">← Fleet</a>
    <div class="title">
      <span class="name">{session.name || session.session_id.slice(0, 8)}</span>
      <span class="muted small">{session.harness} on {session.machine}{session.kind === 'background' ? ' · background' : ''}</span>
    </div>
    <div class={`state small ${session.status}`}>{statusLabel(session.status, session.waiting_for)}</div>
    <div class="cwd muted small">{shortCwd(session.cwd)}</div>
    <SessionActions {session} />
    <div class="tools small">
      <label><input type="checkbox" bind:checked={showThinking} /> thinking</label>
      <label><input type="checkbox" bind:checked={showMeta} /> system context</label>
      {#if session.native_url}
        <a href={session.native_url} target="_blank" rel="noopener">Open in Claude app</a>
      {/if}
    </div>
  </div>

  <div class="list" bind:this={list} onscroll={onScroll}>
    {#if visible.length === 0}
      <p class="notice muted">
        {session.transcript_path ? 'No messages yet.' : 'No transcript is available for this session.'}
      </p>
    {/if}
    {#each visible as m (m.id)}
      <MessageItem {m} />
    {/each}
  </div>

  <Composer disabled={!canSend} sendState={sendState} error={sendError} onsend={send}
    hint={canSend ? '' : 'This session cannot receive prompts from here.'} />
{/if}

<style>
  .head {
    position: sticky;
    top: 48px;
    z-index: 3;
    background: var(--surface);
    border-bottom: 1px solid var(--hairline);
    padding: 10px 16px;
    display: grid;
    gap: 2px;
  }
  .back { font-size: 13px; }
  .title { display: flex; gap: 8px; align-items: baseline; }
  .name { font-weight: 600; font-size: 17px; }
  .state.waiting { color: var(--signal); font-weight: 500; }
  .state.busy { color: var(--cobalt); }
  .tools { display: flex; gap: 14px; margin-top: 4px; color: var(--muted); }
  .tools label { display: inline-flex; gap: 4px; align-items: center; }
  .list { padding: 8px 0 16px; }
  .notice { padding: 16px; }
  @media (min-width: 960px) {
    .head { top: 0; }
    .list { height: calc(100vh - 48px - 118px - 96px); overflow-y: auto; }
  }
</style>
