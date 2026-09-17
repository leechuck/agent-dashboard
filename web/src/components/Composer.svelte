<script lang="ts">
  import { fleet } from '../lib/store.svelte'
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
    if (e.key === 'Escape') {
      box?.blur() // hands the arrow keys back to the session switcher
      return
    }
    if (e.key !== 'Enter' || e.isComposing) return
    if (e.metaKey || e.ctrlKey || (!touch && !e.shiftKey)) submit(e)
  }
</script>

<form class="composer" onsubmit={submit}>
  <textarea rows="2" bind:this={box} value={text} oninput={(e) => fleet.setDraft(sessionKey, e.currentTarget.value)} placeholder={disabled ? hint : 'Send a message to this session'} {disabled} onkeydown={key}></textarea>
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
    display: grid;
    gap: 8px;
  }
  textarea {
    width: 100%;
    resize: vertical;
    padding: 8px 10px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    background: var(--page);
  }
  .actions { display: flex; align-items: center; gap: 8px; }
  .actions > span { flex: 1; min-width: 0; }
</style>
