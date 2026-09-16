<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { currentSubscription, disablePush, enablePush, pushSupported } from '../lib/push'

  let subscribed = $state(false)
  let msg = $state('')
  const theme = $state({ value: localStorage.getItem('theme') ?? 'auto' })

  onMount(async () => {
    subscribed = !!(await currentSubscription())
  })

  async function toggle() {
    msg = ''
    if (subscribed) {
      await disablePush()
      subscribed = false
      return
    }
    const r = await enablePush()
    if (r === 'ok') subscribed = true
    else if (r === 'denied') msg = 'Notifications are blocked for this site in the browser.'
    else if (r === 'unsupported') msg = 'This browser cannot receive push notifications. On Android, install the app from the browser menu first.'
    else msg = 'The hub has no push keys configured.'
  }

  async function test() {
    await api.pushTest()
    msg = 'Test notification sent.'
  }

  function setTheme(v: string) {
    theme.value = v
    localStorage.setItem('theme', v)
    if (v === 'auto') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = v
  }
</script>

<section>
  <h1>Settings</h1>
  <div class="row">
    <div>
      <div class="lead">Notifications on this device</div>
      <div class="small muted">Pushed when a session needs a decision, asks a question, or a background task finishes.</div>
    </div>
    <button class:primary={!subscribed} onclick={toggle} disabled={!pushSupported()}>
      {subscribed ? 'Turn off' : 'Turn on'}
    </button>
  </div>
  {#if subscribed}
    <div class="row"><span class="small muted">Send a test notification</span><button onclick={test}>Send test</button></div>
  {/if}
  {#if msg}<p class="small">{msg}</p>{/if}

  <div class="row">
    <div class="lead">Theme</div>
    <div class="seg">
      {#each ['auto', 'light', 'dark'] as v}
        <button class:primary={theme.value === v} onclick={() => setTheme(v)}>{v}</button>
      {/each}
    </div>
  </div>
</section>

<style>
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--hairline); }
  .lead { font-weight: 500; }
  .seg { display: flex; gap: 6px; }
  p { padding: 0 16px; }
</style>
