<script lang="ts">
  import { api } from '../lib/api'
  let token = $state('')
  let error = $state('')
  async function go(e: Event) {
    e.preventDefault()
    try {
      await api.login(token)
      location.hash = '#/'
      location.reload()
    } catch {
      error = 'That token was not accepted.'
    }
  }
</script>

<form onsubmit={go}>
  <h1>Sign in</h1>
  <p class="muted">Paste the web token from <code>~/.agentdash/.env</code> on the hub.</p>
  <input type="password" bind:value={token} placeholder="token" autocomplete="current-password" />
  <button class="primary" type="submit">Sign in</button>
  {#if error}<p class="err">{error}</p>{/if}
</form>

<style>
  form { max-width: 360px; margin: 64px auto; padding: 0 16px; display: grid; gap: 12px; }
  h1 { font-size: 22px; margin: 0; }
  input { padding: 8px 10px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); }
  .err { color: var(--signal); margin: 0; }
</style>
