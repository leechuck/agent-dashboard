<script lang="ts">
  import type { Session } from '../lib/types'
  import { ago, shortCwd, statusLabel } from '../lib/format'
  let { session, selected }: { session: Session; selected: boolean } = $props()
  const s = $derived(session)
</script>

<li class={`row ${s.status}`} class:selected>
  <a href={`#/session/${encodeURIComponent(s.key)}`}>
    <div class="top">
      <span class="name">{s.name || s.session_id.slice(0, 8)}</span>
      <span class="harness">{s.harness}{s.provider && s.provider !== 'anthropic' && s.provider !== 'openai' ? ` via ${s.provider}` : ''}</span>
      <span class="age muted">{ago(s.updated_at)}</span>
    </div>
    <div class="mid muted small">{shortCwd(s.cwd)}</div>
    <div class="state small">{statusLabel(s.status, s.waiting_for)}</div>
    {#if s.last_line}
      <div class="last small">{s.last_line}</div>
    {/if}
  </a>
</li>

<style>
  .row { border-bottom: 1px solid var(--hairline); border-left: 3px solid var(--hairline); }
  .row.busy { border-left-color: var(--cobalt); animation: pulse 2.4s ease-in-out infinite; }
  .row.waiting { border-left-color: var(--signal); }
  .row.offline, .row.done, .row.stopped { border-left-style: dashed; }
  .row.selected { background: var(--cobalt-soft); }
  a { display: block; padding: 10px 16px 12px 13px; color: inherit; text-decoration: none; }
  a:hover { background: var(--page); text-decoration: none; }
  .top { display: flex; gap: 8px; align-items: baseline; }
  .name { font-weight: 600; }
  .harness { color: var(--muted); font-size: 13px; }
  .age { margin-left: auto; font-size: 13px; }
  .state { margin-top: 2px; }
  .waiting .state { color: var(--signal); font-weight: 500; }
  .busy .state { color: var(--cobalt); }
  .last { margin-top: 4px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @keyframes pulse { 0%, 100% { border-left-color: var(--cobalt); } 50% { border-left-color: var(--cobalt-soft); } }
</style>
