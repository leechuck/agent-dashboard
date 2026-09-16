<script lang="ts">
  import type { Message } from '../lib/types'
  import { clock } from '../lib/format'
  let { m }: { m: Message } = $props()
  let open = $state(false)

  function summary(m: Message): string {
    const i = m.tool_input ?? {}
    if (m.tool_name === 'Bash') return String(i.command ?? '')
    if (m.tool_name === 'Read' || m.tool_name === 'Write' || m.tool_name === 'Edit')
      return String(i.file_path ?? '')
    if (m.tool_name === 'Agent') return String(i.description ?? '')
    if (m.tool_name === 'Skill') return String(i.skill ?? '')
    const first = Object.values(i).find((v) => typeof v === 'string') as string | undefined
    return first ?? ''
  }
</script>

{#if m.kind === 'text'}
  <div class={`msg ${m.role}`} class:meta={m.is_meta}>
    <div class="who small muted">{m.role === 'user' ? 'you' : m.role === 'assistant' ? 'agent' : m.role}{m.agent_id ? ' (subagent)' : ''} {clock(m.ts)}</div>
    <pre class="text">{m.text}</pre>
  </div>
{:else if m.kind === 'thinking'}
  <div class="msg thinking">
    <div class="who small muted">thinking</div>
    <pre class="text muted">{m.text}</pre>
  </div>
{:else if m.kind === 'tool_use'}
  <div class="tool">
    <button class="tline" onclick={() => (open = !open)}>
      <span class="tname">{m.tool_name}</span>
      <span class="tsum mono">{summary(m)}</span>
    </button>
    {#if open}
      <pre class="mono detail">{JSON.stringify(m.tool_input, null, 2)}</pre>
    {/if}
  </div>
{:else if m.kind === 'tool_result'}
  <div class="tool result" class:error={m.is_error}>
    <button class="tline" onclick={() => (open = !open)}>
      <span class="tname muted">{m.is_error ? 'error' : 'result'}</span>
      <span class="tsum mono">{m.text.split('\n')[0].slice(0, 120)}</span>
    </button>
    {#if open}
      <pre class="mono detail">{m.text}</pre>
    {/if}
  </div>
{:else}
  <div class="msg system"><pre class="text muted small">{m.text}</pre></div>
{/if}

<style>
  .msg { padding: 8px 16px; }
  .msg.user { background: var(--cobalt-soft); border-left: 3px solid var(--cobalt); margin: 8px 0; }
  .msg.meta .text { color: var(--muted); font-size: 13px; }
  .text { font-family: var(--sans); font-size: 15px; }
  .thinking .text { font-size: 14px; }
  .tool { padding: 2px 16px; }
  .tline {
    display: flex;
    gap: 8px;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    padding: 4px 0;
    border-radius: 0;
    overflow: hidden;
  }
  .tline:hover { border: none; background: var(--page); }
  .tname { font-weight: 500; flex: none; }
  .tsum { color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .result .tsum { color: var(--muted); }
  .error .tname { color: var(--signal); }
  .detail { padding: 8px 10px; background: var(--page); border: 1px solid var(--hairline); border-radius: var(--radius); max-height: 50vh; overflow: auto; }
</style>
