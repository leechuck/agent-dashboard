<script lang="ts">
  let {
    disabled,
    sendState,
    error,
    hint,
    onsend,
    draft = '',
  }: {
    disabled: boolean
    sendState: 'idle' | 'sending' | 'sent' | 'failed'
    error: string
    hint: string
    onsend: (text: string) => void
    draft?: string
  } = $props()
  let text = $state('')
  $effect(() => {
    if (draft) text = draft
  })
  function submit(e: Event) {
    e.preventDefault()
    if (!text.trim() || disabled) return
    onsend(text.trim())
    text = ''
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e)
  }
</script>

<form class="composer" onsubmit={submit}>
  <textarea rows="2" bind:value={text} placeholder={disabled ? hint : 'Send a message to this session'} {disabled} onkeydown={key}></textarea>
  <div class="actions">
    <span class="small muted">
      {#if sendState === 'sending'}Sending…{:else if sendState === 'sent'}Delivered.{:else if sendState === 'failed'}Not delivered: {error}{:else if !disabled}Ctrl+Enter sends.{/if}
    </span>
    <button class="primary" type="submit" disabled={disabled || !text.trim()}>Send</button>
  </div>
</form>

<style>
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
  .actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
</style>
