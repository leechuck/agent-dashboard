<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { currentSubscription, disablePush, enablePush, pushSupported } from '../lib/push'

  import type { AgentSettingsView } from '../lib/types'
  let subscribed = $state(false)
  let av = $state<AgentSettingsView | null>(null)
  let saved = $state('')
  const modelHints: Record<string, string[]> = {
    claude: ['sonnet', 'haiku', 'opus', 'fable'],
    codex: [],
    api: ['anthropic/claude-sonnet-5', 'anthropic/claude-haiku-4.5', 'openai/gpt-5-mini', 'google/gemini-3.8-flash'],
  }
  async function loadAgents() {
    try {
      av = await api.agentSettings()
    } catch {
      av = null
    }
  }
  async function saveAgents() {
    if (!av) return
    saved = ''
    try {
      const r = await api.saveAgentSettings(av.agents)
      av.agents = r.agents
      saved = 'Saved.'
    } catch (e) {
      saved = String(e)
    }
  }
  let msg = $state('')
  const theme = $state({ value: localStorage.getItem('theme') ?? 'auto' })

  onMount(async () => {
    loadAgents()
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

  {#if av}
    <h2>Agents the dashboard uses</h2>
    <p class="small muted">These run only for the dashboard's own features. They use the login of the chosen machine; nothing is stored on the hub.</p>

    <div class="card">
      <div class="lead">Advice <span class="small muted">(Overview, on request only)</span></div>
      <div class="grid">
        <label>Agent
          <select bind:value={av.agents.advice.harness}>
            <option value="claude">Claude Code (subscription)</option>
            <option value="codex">Codex (subscription)</option>
            <option value="api">API endpoint (key on the machine)</option>
          </select>
        </label>
        <label>Model
          <input list="advice-models" bind:value={av.agents.advice.model} placeholder={av.agents.advice.harness === 'codex' ? 'Codex default' : 'sonnet'} />
          <datalist id="advice-models">{#each modelHints[av.agents.advice.harness] ?? [] as m}<option value={m}></option>{/each}</datalist>
        </label>
        <label>Thinking
          <select bind:value={av.agents.advice.effort}>
            {#each ['low', 'medium', 'high'] as e}<option value={e}>{e}</option>{/each}
          </select>
        </label>
        <label>Runs on
          <select bind:value={av.agents.advice.machine}>
            <option value="">any machine</option>
            {#each av.machines as m}<option value={m}>{m}</option>{/each}
          </select>
        </label>
        {#if av.agents.advice.harness === 'claude' && av.logins.length > 1}
          <label>Claude login
            <select bind:value={av.agents.advice.login}>
              <option value="">default</option>
              {#each av.logins as l}<option value={l.dir}>{l.account}</option>{/each}
            </select>
          </label>
        {/if}
      </div>
    </div>

    <div class="card">
      <div class="lead">Personal briefing <span class="small muted">(Claude Code session in ~/pa)</span></div>
      <div class="grid">
        <label>Model
          <input list="pa-models" bind:value={av.agents.personal.model} placeholder="the machine's default" />
          <datalist id="pa-models">{#each modelHints.claude as m}<option value={m}></option>{/each}</datalist>
        </label>
        <label>Runs on
          <select bind:value={av.agents.personal.machine}>
            <option value="">wherever ~/pa is</option>
            {#each av.machines as m}<option value={m}>{m}</option>{/each}
          </select>
        </label>
        {#if av.logins.length > 1}
          <label>Claude login
            <select bind:value={av.agents.personal.login}>
              <option value="">default</option>
              {#each av.logins as l}<option value={l.dir}>{l.account}</option>{/each}
            </select>
          </label>
        {/if}
      </div>
    </div>

    <div class="card">
      <div class="lead">Session titles</div>
      <div class="grid">
        <label class="check"><input type="checkbox" bind:checked={av.agents.titles.enabled} /> Name sessions after what they are working on</label>
        <label>Model
          <input list="title-models" bind:value={av.agents.titles.model} placeholder="haiku" />
          <datalist id="title-models">{#each modelHints[av.agents.advice.harness] ?? [] as m}<option value={m}></option>{/each}</datalist>
        </label>
      </div>
      <p class="small muted inner">One small call, only when a session is new or was asked something new, at most every 15 minutes. Uses the advice agent and machine above with this model.</p>
    </div>

    <div class="row"><span class="small muted">{saved}</span><button class="primary" onclick={saveAgents}>Save</button></div>
  {/if}
</section>

<style>
  h2 { font-size: 15px; margin: 22px 16px 4px; }
  .card { margin: 10px 16px; padding: 12px; background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px 14px; margin-top: 8px; }
  .grid label { display: grid; gap: 3px; font-size: 13px; color: var(--muted); }
  .grid label.check { display: flex; align-items: center; gap: 8px; color: var(--ink); align-self: end; padding-bottom: 6px; }
  .grid select, .grid input:not([type='checkbox']) { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); color: var(--ink); font: inherit; font-size: 14px; }
  .inner { padding: 0; margin: 8px 0 0; }
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--hairline); }
  .lead { font-weight: 500; }
  .seg { display: flex; gap: 6px; }
  p { padding: 0 16px; }
</style>
