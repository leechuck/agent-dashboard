<script lang="ts">
  import type { Message } from '../lib/types'
  import { clock } from '../lib/format'
  import { fleet } from '../lib/store.svelte'
  import { editDiff, renderMarkdown } from '../lib/markdown'
  import { agentQuestions, isQuestionTool } from '../lib/questions'
  import QuestionBlock from './QuestionBlock.svelte'
  let { m, sessionKey = '' }: { m: Message; sessionKey?: string } = $props()
  let open = $state(false)
  let fetching = $state(false)
  /** Long tool calls and results arrive cut short; opening one fetches the rest. */
  async function toggle() {
    open = !open
    if (open && m.slim && sessionKey && !fetching) {
      fetching = true
      try {
        await fleet.fullMessage(sessionKey, m.id)
      } catch {
        /* keep the short version */
      } finally {
        fetching = false
      }
    }
  }

  function summary(m: Message): string {
    const i = m.tool_input ?? {}
    if (m.tool_name === 'Bash') return String(i.command ?? '')
    if (m.tool_name === 'Read' || m.tool_name === 'Write' || m.tool_name === 'Edit') return String(i.file_path ?? '')
    if (m.tool_name === 'Agent' || m.tool_name === 'Task') return String(i.description ?? '')
    if (m.tool_name === 'Skill') return String(i.skill ?? '')
    const first = Object.values(i).find((v) => typeof v === 'string') as string | undefined
    return first ?? ''
  }
  const html = $derived(m.kind === 'text' && m.role === 'assistant' ? renderMarkdown(m.text) : '')
  const isEdit = $derived(m.tool_name === 'Edit' && typeof m.tool_input?.old_string === 'string' && typeof m.tool_input?.new_string === 'string')
  const more = $derived(m.slim ? (fetching ? '\n… loading the rest' : '\n… cut short') : '')
  const questionWaiting = $derived(fleet.sessions[sessionKey]?.status === 'waiting' && !(fleet.messages[sessionKey] ?? []).some(other => other.kind === 'tool_result' && !!m.tool_use_id && other.tool_use_id === m.tool_use_id))
  const questionTerminal = $derived(questionWaiting && fleet.sessions[sessionKey]?.extra?.tmux ? `#/terminal/${encodeURIComponent(sessionKey)}?control=1` : '')
</script>

{#if m.kind === 'text' && m.role === 'user'}
  <div class="msg user" class:meta={m.is_meta} class:sent={!!m.sender} class:pending={m.pending}>
    <span class="mark">{m.sender ? '⤷' : '❯'}</span>
    <div class="body">
      <pre class="plain">{m.text}</pre>
      <span class="when">{#if m.pending}<span class="via queued">queued · the agent has not taken it yet (it is busy or a dialog is open)</span> · {:else if m.sender}<span class="via">{m.sender === 'dashboard' ? 'you, from the dashboard' : `from ${m.sender}`}</span> · {/if}{clock(m.ts)}</span>
    </div>
  </div>
{:else if m.kind === 'text'}
  <div class="msg agent" class:meta={m.is_meta} class:sub={!!m.agent_id}>
    <span class="mark">●</span>
    <div class="body"><div class="md">{@html html}</div><span class="when">{m.agent_id ? 'subagent · ' : ''}{clock(m.ts)}</span></div>
  </div>
{:else if m.kind === 'thinking'}
  <div class="msg thinking">
    <span class="mark">✻</span>
    <div class="body"><pre class="plain">{m.text}</pre></div>
  </div>
{:else if m.kind === 'tool_use' && isQuestionTool(m.tool_name)}
  <div class="question-message">
    <QuestionBlock questions={agentQuestions(m.tool_input)} answerHref={questionTerminal} />
    {#if m.slim}<button class="small" onclick={toggle} disabled={fetching}>{fetching ? 'Loading…' : 'Load full question'}</button>{/if}
    <span class="when">{clock(m.ts)}</span>
  </div>
{:else if m.kind === 'tool_use'}
  <div class="tool">
    <button class="tline" onclick={toggle} aria-expanded={open}>
      <span class="mark call">●</span>
      <span class="tname">{m.tool_name}</span><span class="tsum" class:file-path={['Read', 'Write', 'Edit'].includes(m.tool_name ?? '')}>({summary(m)})</span>
    </button>
    {#if open}
      {#if isEdit}
        <pre class="detail diff"><code>{@html editDiff(String(m.tool_input?.old_string), String(m.tool_input?.new_string))}</code>{more}</pre>
      {:else}
        <pre class="detail">{JSON.stringify(m.tool_input, null, 2)}{more}</pre>
      {/if}
    {/if}
  </div>
{:else if m.kind === 'tool_result'}
  <div class="tool result" class:error={m.is_error}>
    <button class="tline" onclick={toggle} aria-expanded={open}>
      <span class="mark">⎿</span>
      <span class="tsum">{m.is_error ? 'Error: ' : ''}{m.text.split('\n')[0].slice(0, 160) || '(no output)'}</span>
      {#if m.text.includes('\n')}<span class="lines">+{m.text.split('\n').length - 1} lines</span>{/if}
    </button>
    {#if open}<pre class="detail">{m.text}{more}</pre>{/if}
  </div>
{:else}
  <div class="msg system"><span class="mark">·</span><div class="body"><pre class="plain small">{m.text}</pre></div></div>
{/if}

<style>
  /* the look of the agents' own terminals: a prompt mark for you, a bullet for the agent,
     green bullets for tool calls with their output hanging under a corner mark */
  .msg { display: flex; gap: 10px; padding: 7px 16px; }
  .question-message { padding: 0 16px 8px; }
  .mark { flex: none; width: 14px; text-align: center; font-family: var(--mono); color: var(--muted); line-height: 1.55; }
  .body { flex: 1; min-width: 0; }
  .when { display: block; margin-top: 2px; font-size: 11.5px; color: var(--muted); }
  .msg.user { margin: 10px 0 6px; padding-top: 9px; padding-bottom: 9px; background: var(--cobalt-soft); border-left: 3px solid var(--cobalt); }
  .msg.user .mark { color: var(--cobalt); font-weight: 600; }
  .msg.user.sent { border-left-style: dashed; }
  .msg.user.pending { opacity: .75; }
  .via.queued { color: var(--signal); }
  .via { color: var(--cobalt); font-weight: 600; }
  .msg.agent .mark { color: var(--ink); }
  .msg.agent.sub .mark { color: var(--amber); }
  .msg.meta { opacity: .65; }
  .msg.thinking { color: var(--muted); font-style: italic; }
  .msg.thinking .mark { color: var(--amber); font-style: normal; }
  .msg.system { padding-top: 2px; padding-bottom: 2px; color: var(--muted); }
  .plain { margin: 0; font-family: var(--sans); font-size: 15px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .plain.small { font-size: 13px; }

  .md { font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; }
  .md :global(p) { margin: 0 0 8px; }
  .md :global(p:last-child), .md :global(ul:last-child), .md :global(ol:last-child) { margin-bottom: 0; }
  .md :global(h3), .md :global(h4), .md :global(h5), .md :global(h6) { margin: 12px 0 5px; font-size: 15px; font-weight: 600; }
  .md :global(h3) { font-size: 16.5px; }
  .md :global(ul), .md :global(ol) { margin: 0 0 8px; padding-left: 22px; }
  .md :global(li) { margin: 2px 0; }
  .md :global(li.d1) { margin-left: 18px; }
  .md :global(li.d2) { margin-left: 36px; }
  .md :global(li.d3) { margin-left: 54px; }
  .md :global(li.task) { list-style: none; margin-left: -18px; }
  .md :global(.box) { color: var(--cobalt); }
  .md :global(code) { font-family: var(--mono); font-size: 13px; padding: 1px 5px; border-radius: 4px; background: var(--cobalt-soft); color: var(--text-code); }
  .md :global(pre.code) { margin: 6px 0 10px; padding: 10px 12px; background: var(--code-bg); color: var(--code-fg); border-radius: var(--radius); overflow-x: auto; font-size: 12.5px; line-height: 1.5; }
  .md :global(pre.code code) { padding: 0; background: none; color: inherit; font-size: inherit; }
  .md :global(a) { color: var(--text-link); text-decoration: underline; text-underline-offset: 2px; }
  .md :global(strong) { font-weight: 600; }
  .md :global(blockquote) { margin: 6px 0; padding: 2px 12px; border-left: 3px solid var(--hairline); color: var(--muted); }
  .md :global(hr) { border: 0; border-top: 1px solid var(--hairline); margin: 12px 0; }
  .md :global(.tablewrap) { overflow-x: auto; margin: 6px 0 10px; }
  .md :global(table) { border-collapse: collapse; font-size: 13.5px; }
  .md :global(th), .md :global(td) { border: 1px solid var(--hairline); padding: 4px 9px; text-align: left; vertical-align: top; }
  .md :global(th) { background: var(--page); font-weight: 600; }

  .tool { padding: 0 16px; font-family: var(--mono); font-size: 13px; }
  .tline { display: flex; gap: 10px; align-items: baseline; width: 100%; text-align: left; background: none; border: none; padding: 3px 0; border-radius: 0; overflow: hidden; font: inherit; }
  .tline:hover { border: none; background: var(--page); }
  .mark.call { color: var(--moss); }
  .tname { font-weight: 600; flex: none; margin-right: -10px; }
  .tsum { color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .result .tline { padding-left: 14px; }
  .lines { flex: none; color: var(--muted); opacity: .7; font-size: 12px; }
  .error .tsum, .error .mark { color: var(--signal); }
  .detail { margin: 2px 0 8px 24px; padding: 8px 10px; background: var(--code-bg); color: var(--code-fg); border-radius: var(--radius); max-height: 50vh; overflow: auto; font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
  .error .detail { border-left: 3px solid var(--signal); }
  .md :global(pre.diff .add), .detail :global(.add) { display: inline-block; width: 100%; background: rgb(46 160 67 / .22); color: var(--moss); }
  .md :global(pre.diff .del), .detail :global(.del) { display: inline-block; width: 100%; background: rgb(248 81 73 / .2); color: var(--signal); }
  .md :global(pre.diff .hunk) { color: var(--cobalt); }
</style>
