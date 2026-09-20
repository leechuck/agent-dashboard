<script lang="ts">
  import type { Decision } from '../lib/types'
  import { fleet } from '../lib/store.svelte'
  import { ago, shortCwd } from '../lib/format'
  import { agentQuestions } from '../lib/questions'
  import QuestionBlock from './QuestionBlock.svelte'

  let { d, compact = false }: { d: Decision; compact?: boolean } = $props()
  let busy = $state(false)
  let err = $state('')
  let open = $state(false)

  const input = $derived(d.tool_input ?? {})
  const summary = $derived.by(() => {
    if (d.tool_name === 'Bash') return String(input.command ?? '')
    for (const k of ['file_path', 'path', 'url', 'description', 'skill', 'prompt', 'pattern']) {
      if (typeof input[k] === 'string') return String(input[k])
    }
    return d.reason || ''
  })
  const where = $derived(d.session_name || d.session_key.split(':').pop()?.slice(0, 8))
  const isEdit = $derived(d.tool_name === 'Edit' && typeof input.old_string === 'string')
  const inTmux = $derived(!!fleet.sessions[d.session_key]?.extra?.tmux)

  async function act(behavior: 'allow' | 'deny', remember = false) {
    busy = true
    err = ''
    try {
      await fleet.answer(d.id, behavior, remember)
    } catch (e) {
      err = String(e)
    } finally {
      busy = false
    }
  }
</script>

<div class="card" class:compact class:pending={d.status === 'pending'} class:question={d.kind === 'question'}>
  <div class="head">
    <span class="tool">{d.kind === 'question' ? 'Question' : d.tool_name}</span>
    <a class="sess" href={`#/session/${encodeURIComponent(d.session_key)}`}>{where} on {d.machine}</a>
    <span class="age muted small">{ago(d.created_at)} ago</span>
  </div>
  {#if d.kind === 'question'}
    <QuestionBlock questions={agentQuestions(d.tool_input, d.question)} answerHref={inTmux ? `#/terminal/${encodeURIComponent(d.session_key)}?control=1` : d.native_url || ''} external={!inTmux} />
  {:else if isEdit}
    <div class="path mono small">{String(input.file_path)}</div>
    <pre class="diff mono"><span class="del">{String(input.old_string)}</span><span class="add">{String(input.new_string ?? '')}</span></pre>
  {:else}
    <pre class="cmd mono">{summary}</pre>
  {/if}
  {#if d.reason && d.kind !== 'question'}
    <div class="reason small muted">{d.reason}</div>
  {/if}
  {#if !compact}
    <div class="cwd small muted">{shortCwd(d.cwd)}</div>
  {/if}

  {#if d.status === 'pending' && d.kind !== 'question'}
    <div class="actions">
      <button class="primary" disabled={busy} onclick={() => act('allow')}>Allow</button>
      <button class="danger" disabled={busy} onclick={() => act('deny')}>Deny</button>
      {#if d.tool_name !== 'Bash'}
        <button disabled={busy} onclick={() => act('allow', true)}>Allow {d.tool_name} for this session</button>
      {/if}
      {#if d.tool_input && !isEdit}
        <button class="ghost" onclick={() => (open = !open)}>{open ? 'Hide' : 'Details'}</button>
      {/if}
    </div>
    {#if d.expires_at}
      <div class="small muted">Falls back to the terminal in {ago(Date.now() * 2 - d.expires_at)}.</div>
    {/if}
  {:else if d.kind === 'question'}
    {#if !inTmux && !d.native_url}<div class="actions">
        <span class="small muted">Answer at the terminal, or turn on Remote Control in that session.</span>
    </div>{/if}
  {:else}
    <div class={`outcome small ${d.status}`}>{d.status}{d.remember ? ' (remembered for the session)' : ''}</div>
  {/if}
  {#if open}
    <pre class="mono detail">{JSON.stringify(d.tool_input, null, 2)}</pre>
  {/if}
  {#if err}<div class="err small">{err}</div>{/if}
</div>

<style>
  .card { padding: 12px 16px; border-bottom: 1px solid var(--hairline); border-left: 3px solid var(--hairline); background: var(--surface); }
  .card.pending { border-left-color: var(--signal); }
  .card.question { border-left-color: var(--amber); }
  .head { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
  .tool { font-weight: 600; }
  .sess { color: var(--muted); font-size: 13px; }
  .age { margin-left: auto; }
  .cmd, .diff { margin: 8px 0; padding: 8px 10px; background: var(--page); border-radius: var(--radius); max-height: 40vh; overflow: auto; }
  .diff .del { display: block; color: var(--signal); }
  .diff .del::before { content: '- '; }
  .diff .add { display: block; color: var(--moss); }
  .diff .add::before { content: '+ '; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .actions button { min-height: 40px; }
  .ghost { border-color: transparent; color: var(--muted); }
  .outcome { margin-top: 6px; color: var(--muted); }
  .outcome.allowed { color: var(--moss); }
  .outcome.denied { color: var(--signal); }
  .detail { margin-top: 8px; padding: 8px 10px; background: var(--page); border-radius: var(--radius); }
  .err { color: var(--signal); margin-top: 6px; }
  .compact .cwd, .compact .reason { display: none; }
</style>
