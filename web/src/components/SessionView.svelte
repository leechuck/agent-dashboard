<script lang="ts">
  import { Fleet, fleet } from '../lib/store.svelte'
  import { api } from '../lib/api'
  import { backendLabel, contextOf, modelLabel, shortCwd, statusLabel } from '../lib/format'
  import MessageItem from './MessageItem.svelte'
  import Composer from './Composer.svelte'
  import SessionActions from './SessionActions.svelte'

  let { key }: { key: string } = $props()
  const session = $derived(fleet.sessions[key])
  const messages = $derived(fleet.messages[key] ?? [])
  const x = $derived((session?.extra ?? {}) as Record<string, any>)
  const ctx = $derived(session ? contextOf(session) : null)
  const parent = $derived(x.parent ? fleet.sessions[x.parent] : undefined)
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

  const canSend = $derived(Fleet.canSend(session))

  async function send(text: string) {
    delete fleet.drafts[key]
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
      <span class="muted small">{backendLabel(session)} on {session.machine}{session.kind === 'background' ? ' · background' : ''}</span>
    </div>
    <div class={`state small ${session.status}`}>{statusLabel(session.status, session.waiting_for)}</div>
    <div class="cwd muted small">{shortCwd(session.cwd)}</div>
    <div class="facts muted small">
      {#if modelLabel(session)}<span>{modelLabel(session)}{x.fast_mode ? ' · fast' : ''}</span>{/if}
      {#if x.effort}<span>thinking {x.effort}</span>{/if}
      {#if ctx}<span class:hotctx={ctx.pct >= 85}>context {ctx.pct.toFixed(0)}%{ctx.window ? ` of ${ctx.window}` : ''}{typeof x.context_tokens === 'number' && x.context_tokens ? ` (${Math.round(x.context_tokens / 1000)}k tokens)` : ''}</span>{/if}
      {#if typeof x.cost_usd === 'number'}<span title="API-price equivalent of this session">≈ ${x.cost_usd.toFixed(2)}</span>{/if}
      {#if parent}<a href={`#/session/${encodeURIComponent(parent.key)}`}>sub-agent of {parent.name || parent.session_id.slice(0, 8)}</a>{/if}
    </div>
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

  <Composer disabled={!canSend} sendState={sendState} error={sendError} onsend={send} draft={fleet.drafts[key] ?? ''}
    hint={canSend ? '' : 'This session cannot receive prompts from here.'} />
{/if}

<style>
  .facts { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 2px; }
  .hotctx { color: var(--signal); }
  .head {
    position: sticky;
    top: var(--sticky-top, 48px);
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
