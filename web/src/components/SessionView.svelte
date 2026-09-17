<script lang="ts">
  import { Fleet, fleet } from '../lib/store.svelte'
  import { api } from '../lib/api'
  import { backendLabel, contextOf, displayName, modelLabel, shortCwd, statusLabel } from '../lib/format'
  import MessageItem from './MessageItem.svelte'
  import Composer from './Composer.svelte'
  import SessionActions from './SessionActions.svelte'
  import SessionSwitcher from './SessionSwitcher.svelte'
  import SwitchPanel from './SwitchPanel.svelte'
  import MovePanel from './MovePanel.svelte'

  let { key }: { key: string } = $props()
  const session = $derived(fleet.sessions[key])
  const messages = $derived(fleet.messages[key] ?? [])
  const loading = $derived(!fleet.messages[key] || !!fleet.loadingMessages[key])
  const x = $derived((session?.extra ?? {}) as Record<string, any>)
  const ctx = $derived(session ? contextOf(session) : null)
  const parent = $derived(x.parent ? fleet.sessions[x.parent] : undefined)
  let showThinking = $state(false)
  let switching = $state(false)
  let moving = $state(false)
  let resumeMsg = $state('')
  const live = $derived(!!session && ['busy', 'idle', 'waiting'].includes(session.status))
  const resumable = $derived(!!session && ['claude', 'codex', 'pi'].includes(session.harness))
  const elsewhere = $derived(Object.values(fleet.machines).some((m) => m.online && session && m.id !== session.machine))
  /** Start this conversation again where it is, with the same agent and settings. */
  async function resume() {
    if (!session) return
    resumeMsg = 'Resuming…'
    try {
      const r = await api.switchSession(key, { harness: session.harness })
      resumeMsg = r.ok ? `Resumed in tmux (${r.attach}); the card comes back to the board shortly.` : r.error ?? 'failed'
    } catch (e) {
      resumeMsg = String(e)
    }
  }
  let renaming = $state(false)
  let newTitle = $state('')
  let titleBusy = $state(false)
  async function saveTitle(title: string) {
    titleBusy = true
    try {
      await api.setTitle(key, title)
    } finally {
      titleBusy = false
      renaming = false
    }
  }
  async function regenerate() {
    titleBusy = true
    try {
      await api.regenerateTitle(key)
    } finally {
      titleBusy = false
    }
  }
  let showMeta = $state(false)
  let list: HTMLElement | undefined = $state()
  let stickBottom = $state(true)
  let sendState = $state<'idle' | 'sending' | 'sent' | 'failed'>('idle')
  let sendError = $state('')

  $effect(() => {
    key
    fleet.ensureSession(key)
    fleet.openSession(key)
    // "#/session/<key>?move=1" opens the move panel (from the History page)
    moving = new URLSearchParams(location.hash.split('?')[1] ?? '').get('move') === '1'
    resumeMsg = ''
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
  /** Why the box is greyed out, and what would change it. */
  const whyNot = $derived.by(() => {
    if (!session) return ''
    if (!['busy', 'idle', 'waiting'].includes(session.status)) return 'This session has ended.'
    if (session.harness === 'claude') return 'This Claude session exposes no message socket (very old version or a headless run).'
    const cmd = session.harness === 'tmux' ? 'the agent' : session.harness
    return `${session.harness} has no way to receive text from outside, unless it runs inside tmux: start it as "agent-tmux ${cmd}" and this box works.`
  })

  /** Returns whether it was delivered; the box keeps the text when it was not. */
  async function send(text: string): Promise<boolean> {
    const target = key
    sendState = 'sending'
    sendError = ''
    try {
      const r = await api.prompt(target, text)
      if (target === key) {
        sendState = r.ok ? 'sent' : 'failed'
        sendError = r.error ?? ''
      }
      return r.ok
    } catch (e) {
      if (target === key) {
        sendState = 'failed'
        sendError = String(e)
      }
      return false
    }
  }
  $effect(() => {
    key
    sendState = 'idle'
    sendError = ''
  })
</script>

<div class="sv">
<SessionSwitcher current={key} />
{#if !session}
  <p class="notice muted">Loading session…</p>
{:else}
  <div class="head">
    <a class="back" href="#/">← Fleet</a>
    <div class="title">
      {#if renaming}
        <form class="rename" onsubmit={(e) => { e.preventDefault(); saveTitle(newTitle) }}>
          <!-- svelte-ignore a11y_autofocus -->
          <input bind:value={newTitle} autofocus onkeydown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); renaming = false } }} />
          <button class="primary" type="submit" disabled={titleBusy}>Save</button>
          <button type="button" onclick={() => saveTitle('')} title="Let the model name it again">Auto</button>
        </form>
      {:else}
        <span class="name">{displayName(session)}</span>
        <button class="tbtn" title="Rename" onclick={() => { newTitle = displayName(session); renaming = true }}>✎</button>
        <button class="tbtn" title="Generate a new title" disabled={titleBusy} onclick={regenerate}>{titleBusy ? '…' : '↻'}</button>
      {/if}
      <span class="muted small">{backendLabel(session)} on {session.machine}{session.kind === 'background' ? ' · background' : ''}</span>
    </div>
    <div class={`state small ${session.status}`}>{statusLabel(session.status, session.waiting_for, !!x.goal)}</div>
    <div class="cwd muted small">{x.title && session.name ? `${session.name} · ` : ''}{shortCwd(session.cwd)}</div>
    {#if x.goal}<div class="goal small"><b>Goal</b> {x.goal}{x.goal_tokens ? ` · ${(x.goal_tokens / 1e6).toFixed(1)}M tokens so far` : ''}</div>{/if}
    <div class="facts muted small">
      {#if modelLabel(session)}<span>{modelLabel(session)}{x.fast_mode ? ' · fast' : ''}</span>{/if}
      {#if x.effort}<span>thinking {x.effort}</span>{/if}
      {#if ctx}<span class:hotctx={ctx.pct >= 85}>context {ctx.pct.toFixed(0)}%{ctx.window ? ` of ${ctx.window}` : ''}{typeof x.context_tokens === 'number' && x.context_tokens ? ` (${Math.round(x.context_tokens / 1000)}k tokens)` : ''}</span>{/if}
      {#if x.subagents?.total}<span>⑂ {x.subagents.total} sub-agent{x.subagents.total === 1 ? '' : 's'}{x.subagents.running ? `, ${x.subagents.running} running${x.subagents.doing?.length ? `: ${x.subagents.doing.join(' · ')}` : ''}` : ''}</span>{/if}
      {#if typeof x.cost_usd === 'number'}<span title="API-price equivalent of this session">≈ ${x.cost_usd.toFixed(2)}</span>{/if}
      {#if parent}<a href={`#/session/${encodeURIComponent(parent.key)}`}>sub-agent of {displayName(parent)}</a>{/if}
    </div>
    <div class="actrow">
      <SessionActions {session} />
      {#if resumable && !live}<button class="swbtn primary" onclick={resume} disabled={resumeMsg.startsWith('Resuming')}>Resume here</button>{/if}
      {#if resumable}<button class="swbtn" onclick={() => { switching = !switching; if (switching) moving = false }}>{live ? 'Switch agent / model…' : 'Resume with another agent / model…'}</button>{/if}
      {#if resumable && elsewhere}<button class="swbtn" onclick={() => { moving = !moving; if (moving) switching = false }}>Move to another machine…</button>{/if}
    </div>
    {#if resumeMsg}<p class="small" class:okmsg={!resumeMsg.startsWith('Resuming') && resumeMsg.startsWith('Resumed')}>{resumeMsg}</p>{/if}
    {#if switching}<SwitchPanel {session} onclose={() => (switching = false)} />{/if}
    {#if moving}<MovePanel {session} onclose={() => (moving = false)} />{/if}
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
        {#if !session.transcript_path}No transcript is available for this session.{:else if loading}<span class="spin"></span> Loading transcript…{:else}No messages yet.{/if}
      </p>
    {/if}
    {#each visible as m (m.id)}
      <MessageItem {m} sessionKey={key} />
    {/each}
  </div>

  <Composer disabled={!canSend} sendState={sendState} error={sendError} onsend={send} sessionKey={key}
    hint={canSend ? '' : whyNot} />
{/if}
</div>

<style>
  .facts { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 2px; }
  .hotctx { color: var(--signal); }
  .tbtn { border: 0; background: none; padding: 0 4px; color: var(--muted); font-size: 14px; }
  .tbtn:hover { color: var(--ink); }
  .rename { display: flex; gap: 6px; flex: 1; }
  .rename input { flex: 1; min-width: 0; padding: 4px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); font: inherit; font-weight: 600; }
  .rename button { font-size: 13px; padding: 3px 10px; }
  .actrow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .swbtn { font-size: 13px; padding: 4px 10px; }
  .okmsg { color: var(--moss); }
  .goal { margin-top: 4px; padding: 5px 8px; background: var(--cobalt-soft); border-radius: var(--radius); max-height: 5.8em; overflow: auto; }
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
  .spin { display: inline-block; width: 11px; height: 11px; margin-right: 6px; border: 2px solid var(--hairline); border-top-color: var(--cobalt); border-radius: 50%; vertical-align: -1px; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (min-width: 960px) {
    /* the pane is a column: switcher and header take what they need, the transcript the rest */
    .sv { height: 100%; display: flex; flex-direction: column; }
    .head { position: static; flex: none; }
    .list { flex: 1 1 0; min-height: 0; overflow-y: auto; }
  }
</style>
