<script lang="ts">
  import type { Snippet } from 'svelte'

  /** Frame of one panel on the Personal tab: a heading that folds, a one-line summary that
   *  stays visible when folded, a refresh button, the panel's own error. */
  let {
    id,
    title,
    summary = '',
    alert = false,
    loading = false,
    error = '',
    onrefresh,
    children,
  }: {
    id: string
    title: string
    summary?: string
    alert?: boolean
    loading?: boolean
    error?: string
    onrefresh?: () => void
    children: Snippet
  } = $props()

  function stored(): boolean {
    try {
      return localStorage.getItem(`pa.fold.${id}`) === '1'
    } catch {
      return false
    }
  }
  let folded = $state(stored())
  function toggle() {
    folded = !folded
    try {
      localStorage.setItem(`pa.fold.${id}`, folded ? '1' : '0')
    } catch {
      /* private window: the fold just does not persist */
    }
  }
</script>

<section class="panel" class:folded>
  <div class="head">
    <button class="fold" onclick={toggle} aria-expanded={!folded}>
      <span class="caret" aria-hidden="true">{folded ? '▸' : '▾'}</span>
      <h2>{title}</h2>
      {#if summary}<span class="sum small" class:alert>{summary}</span>{/if}
    </button>
    {#if onrefresh}
      <button class="ghost small" onclick={onrefresh} disabled={loading} title="Ask the laptop again">{loading ? '…' : '↻'}</button>
    {/if}
  </div>
  {#if !folded}
    {#if error}<p class="small err">{error}</p>{/if}
    {@render children()}
  {/if}
</section>

<style>
  .panel { padding: 0 16px; border-top: 1px solid var(--hairline); }
  .panel:first-child { border-top: 0; }
  .head { display: flex; align-items: center; gap: 6px; }
  .fold { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; border: 0; background: none; padding: 14px 0 8px; text-align: left; border-radius: 0; }
  .folded .fold { padding-bottom: 14px; }
  .caret { color: var(--muted); font-size: 11px; width: 10px; }
  h2 { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); white-space: nowrap; }
  .sum { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sum.alert { color: var(--signal); }
  .ghost { border-color: transparent; background: none; color: var(--muted); padding: 2px 8px; }
  .ghost:hover { color: var(--ink); border-color: var(--hairline); }
  .err { color: var(--signal); margin: 0 0 8px; }
</style>
