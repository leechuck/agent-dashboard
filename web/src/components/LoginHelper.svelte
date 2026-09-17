<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'

  // Claude's login in a pane, as buttons, a link and a box for the code: the terminal
  // is hard to use for this (a long address to copy, a code to paste, a phone keyboard).
  let { paneKey }: { paneKey: string } = $props()
  type Screen = { stage: string; url?: string; options?: { n: number; label: string; chosen: boolean }[]; continue?: boolean }
  let screen = $state<Screen | null>(null)
  let code = $state('')
  let busy = $state(false)
  let err = $state('')
  let copied = $state(false)

  async function look() {
    try {
      const r = await api.paneScreen(paneKey)
      if (r.ok) screen = r.login
    } catch {
      /* the terminal is still there */
    }
  }
  async function press(keys: string[], text = '') {
    busy = true
    err = ''
    try {
      const r = await api.paneKeys(paneKey, keys, text)
      if (!r.ok) err = r.error ?? 'failed'
      setTimeout(look, 900)
    } catch (e) {
      err = String(e)
    } finally {
      busy = false
    }
  }
  /** Menus start on option 1 with the cursor wherever it is: move up to the top, then down. */
  function choose(n: number, options: { n: number; chosen: boolean }[]) {
    const at = options.find((o) => o.chosen)?.n ?? 1
    const moves = n > at ? Array(n - at).fill('Down') : Array(at - n).fill('Up')
    press([...moves, 'Enter'])
  }
  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      copied = true
      setTimeout(() => (copied = false), 2000)
    } catch {
      /* the link can still be opened */
    }
  }
  onMount(() => {
    look()
    const t = setInterval(look, 2500)
    return () => clearInterval(t)
  })
</script>

{#if screen && screen.stage !== 'other'}
  <div class="lh">
    {#if screen.stage === 'theme'}
      <span>First start on this login: Claude asks for a colour theme.</span>
      <button class="primary" disabled={busy} onclick={() => press(['Enter'])}>Keep the default and go on</button>
    {:else if screen.stage === 'method'}
      <span>How to log in:</span>
      {#each screen.options ?? [] as o (o.n)}
        <button class:primary={o.n === 1} disabled={busy} onclick={() => choose(o.n, screen!.options ?? [])}>{o.label}</button>
      {/each}
    {:else if screen.stage === 'browser' || screen.stage === 'code'}
      <div class="step"><b>1.</b> <a class="btn primary" href={screen.url} target="_blank" rel="noopener">Open the sign-in page</a>
        <button onclick={() => copy(screen!.url!)}>{copied ? 'Copied' : 'Copy link'}</button>
        <span class="small muted">sign in with the account this login is for</span></div>
      <form class="step" onsubmit={(e) => { e.preventDefault(); if (code.trim()) press(['Enter'], code.trim()).then(() => (code = '')) }}>
        <b>2.</b> <input bind:value={code} placeholder="Paste the code the page shows" autocomplete="off" spellcheck="false" />
        <button class="primary" type="submit" disabled={busy || !code.trim()}>Send code</button>
      </form>
    {:else if screen.stage === 'trust'}
      <span>Claude asks whether you trust this folder.</span>
      <button class="primary" disabled={busy} onclick={() => press(['Down', 'Enter'])}>Yes, I trust it</button>
    {:else if screen.stage === 'done'}
      <span class="ok">Logged in. Limits shows this plan within a minute.</span>
      {#if screen.continue}<button disabled={busy} onclick={() => press(['Enter'])}>Continue</button>{/if}
    {/if}
    {#if err}<span class="err small">{err}</span>{/if}
  </div>
{/if}

<style>
  .lh { display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: center; padding: 10px 16px; background: var(--cobalt-soft); border-bottom: 1px solid var(--hairline); }
  .step { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; width: 100%; }
  .step input { flex: 1 1 260px; padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); font-family: var(--mono, monospace); }
  button, .btn { font-size: 13px; padding: 5px 12px; }
  .btn { border: 1px solid var(--cobalt); border-radius: var(--radius); }
  .btn.primary { background: var(--cobalt); color: white; text-decoration: none; }
  .ok { color: var(--moss); font-weight: 500; }
  .err { color: var(--signal); }
</style>
