<script lang="ts">
  import { fleet } from '../lib/store.svelte'
  import type { SlashCommand } from '../lib/types'
  let {
    sessionKey,
    disabled,
    sendState,
    error,
    hint,
    onsend,
  }: {
    sessionKey: string
    disabled: boolean
    sendState: 'idle' | 'sending' | 'sent' | 'failed'
    error: string
    hint: string
    onsend: (text: string) => Promise<boolean>
  } = $props()
  // what is typed belongs to the session, not to this box: it survives switching cards
  // and reloads, and goes away when it is sent or reset
  const text = $derived(fleet.drafts[sessionKey] ?? '')
  let box: HTMLTextAreaElement | undefined = $state()
  // with a real keyboard Enter sends and Shift+Enter breaks the line; on a touch keyboard
  // Enter has to stay a line break, so the button (or Ctrl+Enter) sends there
  const touch = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches

  // ---- slash commands, in two stages: the command, then what may follow it
  const session = $derived(fleet.sessions[sessionKey])
  const inTmux = $derived(!!(session?.extra as Record<string, unknown> | undefined)?.tmux)
  const all = $derived(fleet.commands[sessionKey] ?? [])
  const firstLine = $derived(text.split('\n')[0])
  const onlyLine = $derived(!text.includes('\n'))
  /** null = not completing; otherwise the command being typed and the argument after it. */
  const typing = $derived.by(() => {
    if (!onlyLine || !firstLine.startsWith('/')) return null
    const m = /^\/([\w:-]*)(\s+)?(.*)$/.exec(firstLine)
    if (!m) return null
    return { name: m[1], started: !!m[2], arg: m[3] }
  })
  const command = $derived(typing?.started ? (all.find((c) => c.name === typing.name) ?? null) : null)
  type Row = { insert: string; label: string; note: string; tag: string }
  const rows = $derived.by((): Row[] => {
    if (!typing) return []
    if (command) {
      const q = typing.arg.toLowerCase()
      return command.options
        .filter((o) => o.value.toLowerCase().includes(q) || o.label.toLowerCase().includes(q))
        .sort((a, b) => Number(b.value.toLowerCase().startsWith(q)) - Number(a.value.toLowerCase().startsWith(q)))
        .slice(0, 10)
        .map((o) => ({ insert: `/${command.name} ${o.value}`, label: o.value, note: o.label === o.value ? '' : o.label, tag: '' }))
    }
    if (typing.started) return []
    const q = typing.name.toLowerCase()
    return all
      .filter((c) => c.name.toLowerCase().includes(q))
      .sort((a, b) => Number(b.name.toLowerCase().startsWith(q)) - Number(a.name.toLowerCase().startsWith(q)))
      .slice(0, 8)
      .map((c) => ({ insert: `/${c.name}${c.args ? ' ' : ''}`, label: `/${c.name}`, note: c.description, tag: c.source === 'builtin' ? '' : c.source }))
  })
  /** The hint under the box while an argument is being typed, when there is no list. */
  const argHint = $derived(command && !rows.length ? command.args || '' : '')
  let picked = $state(0)
  let menu: HTMLElement | undefined = $state()
  $effect(() => {
    if (typing) fleet.loadCommands(sessionKey)
  })
  $effect(() => {
    typing?.name
    typing?.started
    picked = 0
  })
  $effect(() => {
    picked
    queueMicrotask(() => menu?.querySelector('.on')?.scrollIntoView({ block: 'nearest' }))
  })
  function complete(r: Row) {
    fleet.setDraft(sessionKey, r.insert)
    box?.focus()
  }

  $effect(() => {
    sessionKey
    // ready to type on arrival; not on touch screens, where focus throws up the keyboard
    if (!touch && !disabled) queueMicrotask(() => box?.focus({ preventScroll: true }))
  })
  async function submit(e: Event) {
    e.preventDefault()
    const key = sessionKey
    if (!text.trim() || disabled || sendState === 'sending') return
    if (await onsend(text.trim())) fleet.setDraft(key, '')
  }
  function key(e: KeyboardEvent) {
    if (rows.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        picked = (picked + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length
        e.preventDefault()
        return
      }
      // Tab always completes; Enter completes the command, then sends the finished line
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !touch && !command)) {
        complete(rows[picked])
        e.preventDefault()
        return
      }
      if (e.key === 'Escape') {
        fleet.setDraft(sessionKey, '')
        e.preventDefault()
        return
      }
    }
    if (e.key === 'Escape') {
      box?.blur() // hands the arrow keys back to the session switcher
      return
    }
    if (e.key !== 'Enter' || e.isComposing) return
    if (e.metaKey || e.ctrlKey || (!touch && !e.shiftKey)) submit(e)
  }
</script>

<form class="composer" onsubmit={submit}>
  {#if rows.length}
    <div class="menu" bind:this={menu} role="listbox" aria-label={command ? `Values for /${command.name}` : 'Commands'}>
      {#if command}<div class="head small muted">/{command.name} {command.args}</div>{/if}
      {#each rows as r, i (r.insert)}
        <button type="button" class="item" class:on={i === picked} role="option" aria-selected={i === picked}
          onmouseenter={() => (picked = i)} onclick={() => complete(r)}>
          <span class="cname">{r.label}</span>
          <span class="cdesc">{r.note}</span>
          {#if r.tag}<span class="csrc">{r.tag}</span>{/if}
        </button>
      {/each}
      <div class="foot small muted">
        ↑↓ choose · Tab completes{command ? ' · Enter sends' : ''}{inTmux ? '' : ' · this session is not in tmux, so a command cannot be delivered'}
      </div>
    </div>
  {:else if argHint}
    <div class="menu"><div class="foot small muted">/{command?.name} {argHint} · Enter sends</div></div>
  {:else if typing && !typing.started && !all.length}
    <div class="menu"><div class="foot small muted">Looking up the commands this session accepts…</div></div>
  {/if}
  <textarea rows="2" bind:this={box} value={text} oninput={(e) => fleet.setDraft(sessionKey, e.currentTarget.value)} placeholder={disabled ? hint : 'Send a message, or / for a command'} {disabled} onkeydown={key}></textarea>
  <div class="actions">
    <span class="small muted">
      {#if sendState === 'sending'}Sending…{:else if sendState === 'sent'}Delivered.{:else if sendState === 'failed'}Not delivered: {error}{:else if !disabled}{touch ? 'Ctrl+Enter or the button sends.' : 'Enter sends, Shift+Enter for a new line.'}{/if}
    </span>
    {#if text}<button type="button" class="reset" onclick={() => { fleet.setDraft(sessionKey, ''); box?.focus() }}>Reset</button>{/if}
    <button class="primary" type="submit" disabled={disabled || !text.trim() || sendState === 'sending'}>Send</button>
  </div>
</form>

<style>
  .reset { color: var(--muted); }
  .composer {
    position: sticky;
    bottom: 0;
    background: var(--surface);
    border-top: 1px solid var(--hairline);
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  }
  textarea { width: 100%; resize: vertical; padding: 10px 12px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); font: inherit; font-size: 15px; }
  textarea:focus { outline: none; border-color: var(--cobalt); box-shadow: 0 0 0 2px var(--cobalt-soft); }
  .actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  .actions > span { flex: 1; min-width: 0; }
  .menu { position: absolute; left: 16px; right: 16px; bottom: calc(100% - 6px); max-height: 46vh; overflow-y: auto; background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); box-shadow: 0 10px 30px rgb(0 0 0 / .16); z-index: 6; }
  .item { display: grid; grid-template-columns: minmax(90px, auto) 1fr auto; gap: 10px; align-items: baseline; width: 100%; text-align: left; border: 0; border-radius: 0; background: none; padding: 6px 12px; font: inherit; font-size: 13.5px; }
  .item.on { background: var(--cobalt-soft); }
  .cname { font-family: var(--mono); font-weight: 600; white-space: nowrap; }
  .cargs { font-weight: 400; color: var(--muted); }
  .cdesc { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .csrc { font-size: 11px; color: var(--muted); border: 1px solid var(--hairline); border-radius: 8px; padding: 0 6px; white-space: nowrap; }
  .head { padding: 6px 12px 4px; border-bottom: 1px solid var(--hairline); font-family: var(--mono); }
  .foot { padding: 5px 12px 7px; border-top: 1px solid var(--hairline); position: sticky; bottom: 0; background: var(--surface); }
  @media (max-width: 600px) { .cdesc { display: none; } }
</style>
