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

  // ---- slash commands: offered while the first line is being typed as "/name"
  const session = $derived(fleet.sessions[sessionKey])
  const inTmux = $derived(!!(session?.extra as Record<string, unknown> | undefined)?.tmux)
  const typedCommand = $derived(/^\/([\w:-]*)$/.exec(text.split('\n')[0]) && !text.includes('\n') ? /^\/([\w:-]*)/.exec(text)![1] : null)
  const all = $derived(fleet.commands[sessionKey] ?? [])
  const matches = $derived.by(() => {
    if (typedCommand === null) return []
    const q = typedCommand.toLowerCase()
    return all
      .filter((c) => c.name.toLowerCase().includes(q))
      .sort((a, b) => Number(b.name.toLowerCase().startsWith(q)) - Number(a.name.toLowerCase().startsWith(q)))
      .slice(0, 8)
  })
  let picked = $state(0)
  let menu: HTMLElement | undefined = $state()
  $effect(() => {
    if (typedCommand !== null) fleet.loadCommands(sessionKey)
  })
  $effect(() => {
    typedCommand
    picked = 0
  })
  $effect(() => {
    picked
    queueMicrotask(() => menu?.querySelector('.on')?.scrollIntoView({ block: 'nearest' }))
  })
  function complete(c: SlashCommand) {
    fleet.setDraft(sessionKey, `/${c.name}${c.args ? ' ' : ''}`)
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
    if (matches.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        picked = (picked + (e.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length
        e.preventDefault()
        return
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !touch)) {
        complete(matches[picked])
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
  {#if matches.length}
    <div class="menu" bind:this={menu} role="listbox" aria-label="Commands">
      {#each matches as c, i (c.name)}
        <button type="button" class="item" class:on={i === picked} role="option" aria-selected={i === picked}
          onmouseenter={() => (picked = i)} onclick={() => complete(c)}>
          <span class="cname">/{c.name}{#if c.args}<span class="cargs"> {c.args}</span>{/if}</span>
          <span class="cdesc">{c.description}</span>
          {#if c.source !== 'builtin'}<span class="csrc">{c.source}</span>{/if}
        </button>
      {/each}
      <div class="foot small muted">
        ↑↓ choose · Tab completes{inTmux ? '' : ' · this session is not in tmux, so a command cannot be delivered'}
      </div>
    </div>
  {:else if typedCommand !== null && !all.length}
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
  .foot { padding: 5px 12px 7px; border-top: 1px solid var(--hairline); position: sticky; bottom: 0; background: var(--surface); }
  @media (max-width: 600px) { .cdesc { display: none; } }
</style>
