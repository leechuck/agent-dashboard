<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { currentSubscription, disablePush, enablePush, pushSupported } from '../lib/push'

  import type { AgentSettingsView, Endpoint } from '../lib/types'
  import { fleet } from '../lib/store.svelte'
  let subscribed = $state(false)
  let av = $state<AgentSettingsView | null>(null)
  let saved = $state('')
  // ----- model choices come from what the machines really offer
  const cat = $derived(fleet.catalog)
  const adviceMachine = $derived(av?.agents.advice.machine || av?.machines[0] || '')
  const mc = $derived(cat?.machines[adviceMachine])
  function modelsFor(harness: string, endpoint: string): { id: string; label: string }[] {
    if (harness === 'api') {
      const st = mc?.endpoints.find((e) => e.id === endpoint)
      const fixed = cat?.endpoints.find((e) => e.id === endpoint)?.models ?? []
      return (st?.models?.length ? st.models : fixed).map((m) => ({ id: m, label: m }))
    }
    return (mc?.models[harness] ?? []).filter((m) => m.id)
  }
  const adviceModels = $derived(av ? modelsFor(av.agents.advice.harness, av.agents.advice.endpoint) : [])
  const claudeModels = $derived((mc?.models.claude ?? []).filter((m) => m.id))
  const apiEndpoints = $derived((cat?.endpoints ?? []).filter((e) => e.base_url))
  const claudeEndpoints = $derived((cat?.endpoints ?? []).filter((e) => e.anthropic_base_url))
  const personalModels = $derived(av ? modelsFor('api', av.agents.personal.endpoint) : [])
  const EFFORTS: Record<string, string[]> = {
    claude: ['low', 'medium', 'high', 'xhigh', 'max'],
    codex: ['minimal', 'low', 'medium', 'high', 'xhigh'],
    api: ['none', 'low', 'medium', 'high'],
  }
  const efforts = (harness: string) => EFFORTS[harness] ?? EFFORTS.api
  // a model or thinking level from the previous agent means nothing to the new one
  function fitAdvice() {
    if (!av) return
    const a = av.agents.advice
    const list = modelsFor(a.harness, a.endpoint)
    if (!list.some((m) => m.id === a.model)) a.model = a.harness === 'api' ? (list[0]?.id ?? '') : ''
    if (!efforts(a.harness).includes(a.effort)) a.effort = 'low'
    if (a.harness !== 'api' && av.agents.titles.model && !modelsFor(a.harness, '').some((m) => m.id === av!.agents.titles.model)) av.agents.titles.model = ''
    if (a.harness === 'api') av.agents.titles.model = ''
  }
  function fitPersonal() {
    if (!av) return
    const p = av.agents.personal
    const list = p.backend === 'endpoint' ? modelsFor('api', p.endpoint) : claudeModels
    if (!list.some((m) => m.id === p.model)) p.model = p.backend === 'endpoint' ? (list[0]?.id ?? '') : ''
  }

  // ----- endpoints
  let endpoints = $state<Endpoint[]>([])
  let epMsg = $state('')
  $effect(() => {
    if (cat && !endpoints.length) endpoints = cat.endpoints.map((e) => ({ ...e }))
  })
  function addEndpoint() {
    endpoints.push({ id: '', name: '', base_url: '', anthropic_base_url: '', key_env: '', wire_api: 'chat' })
  }
  async function saveEndpoints() {
    epMsg = ''
    try {
      await api.saveEndpoints(endpoints.filter((e) => e.id || e.base_url))
      epMsg = 'Saved. Checking them from every machine…'
      await fleet.loadCatalog(true)
      epMsg = 'Saved.'
    } catch (e) {
      epMsg = String(e)
    }
  }
  const epStatus = (id: string) =>
    Object.entries(cat?.machines ?? {}).map(([m, c]) => {
      const st = c.endpoints?.find((x) => x.id === id)
      return { m, ok: !!st && st.reachable && st.key_present && !st.error, why: !st ? 'unknown' : st.error || (st.key_present ? (st.reachable ? `${st.models.length} models` : 'not reachable') : 'key missing') }
    })

  // ----- Claude logins
  let loginName = $state<Record<string, string>>({})
  let loginMsg = $state<Record<string, string>>({})
  async function openLogin(machine: string, name: string) {
    loginMsg[machine] = 'Opening Claude…'
    try {
      const r = await api.openLogin(machine, name)
      if (r.ok && r.terminal_key) {
        loginMsg[machine] = ''
        location.hash = `#/terminal/${encodeURIComponent(r.terminal_key)}?control=1`
      } else loginMsg[machine] = r.error ?? 'failed'
    } catch (e) {
      loginMsg[machine] = String(e)
    }
  }

  let titlesMsg = $state('')
  async function regenerateTitles() {
    titlesMsg = 'Naming…'
    try {
      const r = await api.regenerateTitles()
      titlesMsg = `${r.renamed} renamed.`
    } catch (e) {
      titlesMsg = String(e)
    }
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
    fleet.loadCatalog()
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
          <select bind:value={av.agents.advice.harness} onchange={fitAdvice}>
            <option value="claude">Claude Code (subscription)</option>
            <option value="codex">Codex (subscription)</option>
            <option value="api">One of my endpoints</option>
          </select>
        </label>
        {#if av.agents.advice.harness === 'api'}
          <label>Endpoint
            <select bind:value={av.agents.advice.endpoint} onchange={fitAdvice}>
              <option value="">choose…</option>
              {#each apiEndpoints as e (e.id)}<option value={e.id}>{e.name || e.id}</option>{/each}
            </select>
          </label>
        {/if}
        <label>Model
          {#if av.agents.advice.harness === 'api' && !adviceModels.length}
            <input bind:value={av.agents.advice.model} placeholder="model name on that server" />
          {:else}
            <select bind:value={av.agents.advice.model}>
              {#if av.agents.advice.harness !== 'api'}<option value="">as configured</option>{/if}
              {#each adviceModels as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
              {#if av.agents.advice.model && !adviceModels.some((m) => m.id === av!.agents.advice.model)}<option value={av.agents.advice.model}>{av.agents.advice.model}</option>{/if}
            </select>
          {/if}
        </label>
        <label>Thinking
          <select bind:value={av.agents.advice.effort}>
            {#each efforts(av.agents.advice.harness) as e}<option value={e}>{e === 'none' ? 'off' : e}</option>{/each}
            {#if !efforts(av.agents.advice.harness).includes(av.agents.advice.effort)}<option value={av.agents.advice.effort}>{av.agents.advice.effort}</option>{/if}
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
        <label>Tokens from
          <select bind:value={av.agents.personal.backend} onchange={fitPersonal}>
            <option value="login">Claude subscription</option>
            <option value="endpoint">One of my endpoints</option>
          </select>
        </label>
        {#if av.agents.personal.backend === 'endpoint'}
          <label>Endpoint
            <select bind:value={av.agents.personal.endpoint} onchange={fitPersonal}>
              <option value="">choose…</option>
              {#each claudeEndpoints as e (e.id)}<option value={e.id}>{e.name || e.id}</option>{/each}
            </select>
          </label>
        {/if}
        <label>Model
          {#if av.agents.personal.backend === 'endpoint'}
            {#if personalModels.length}
              <select bind:value={av.agents.personal.model}>
                {#each personalModels as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
                {#if av.agents.personal.model && !personalModels.some((m) => m.id === av!.agents.personal.model)}<option value={av.agents.personal.model}>{av.agents.personal.model}</option>{/if}
              </select>
            {:else}
              <input bind:value={av.agents.personal.model} placeholder="model name on that server" />
            {/if}
          {:else}
            <select bind:value={av.agents.personal.model}>
              <option value="">as configured</option>
              {#each claudeModels as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
              {#if av.agents.personal.model && !claudeModels.some((m) => m.id === av!.agents.personal.model)}<option value={av.agents.personal.model}>{av.agents.personal.model}</option>{/if}
            </select>
          {/if}
        </label>
        <label>Runs on
          <select bind:value={av.agents.personal.machine}>
            <option value="">wherever ~/pa is</option>
            {#each av.machines as m}<option value={m}>{m}</option>{/each}
          </select>
        </label>
        {#if av.agents.personal.backend !== 'endpoint' && av.logins.length > 1}
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
          {#if av.agents.advice.harness === 'api' && !adviceModels.length}
            <input bind:value={av.agents.titles.model} placeholder="same as advice" />
          {:else}
            <select bind:value={av.agents.titles.model}>
              <option value="">{av.agents.advice.harness === 'api' ? 'same as advice' : 'small (haiku)'}</option>
              {#each adviceModels as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
              {#if av.agents.titles.model && !adviceModels.some((m) => m.id === av!.agents.titles.model)}<option value={av.agents.titles.model}>{av.agents.titles.model}</option>{/if}
            </select>
          {/if}
        </label>
        <label>&nbsp;<button type="button" onclick={regenerateTitles}>Name all sessions again</button></label>
      </div>
      {#if titlesMsg}<p class="small muted inner">{titlesMsg}</p>{/if}
      <p class="small muted inner">Titles you typed yourself are kept. One small call, only when a session is new or was asked something new, at most every 15 minutes. Runs with the advice agent, endpoint, thinking and machine above, with this model.</p>
    </div>

    <div class="row"><span class="small muted">{saved}</span><button class="primary" onclick={saveAgents}>Save</button></div>

    <h2>Model endpoints</h2>
    <p class="small muted">Your own or rented model servers. Agents, advice and titles can all run on them. The key never comes here: name the variable that holds it, and put that variable into <code>~/.agentdash/.env</code> on each machine that should use the endpoint.</p>
    {#each endpoints as e, i (i)}
      <div class="card">
        <div class="grid">
          <label>Short id <input bind:value={e.id} placeholder="borg" /></label>
          <label>Name <input bind:value={e.name} placeholder="Qwen on unimatrix01" /></label>
          <label>OpenAI-compatible address <input bind:value={e.base_url} placeholder="http://host:8000/v1" /></label>
          <label>Anthropic-compatible address <span class="hint">(for Claude Code; optional)</span><input bind:value={e.anthropic_base_url} placeholder="http://host:8000" /></label>
          <label>Variable holding the key <input bind:value={e.key_env} placeholder="BORG_LLM_API_KEY" /></label>
          <label>Codex speaks
            <select bind:value={e.wire_api}><option value="chat">chat completions</option><option value="responses">responses</option></select>
          </label>
          <label>Context window <input type="number" bind:value={e.context_window} placeholder="131072" /></label>
          <label>Models <span class="hint">(comma-separated; empty = ask the server)</span><input value={(e.models ?? []).join(', ')} oninput={(ev) => (e.models = ev.currentTarget.value.split(',').map((m) => m.trim()).filter(Boolean))} placeholder="qwen3.8-27b" /></label>
        </div>
        <div class="status small">
          {#each epStatus(e.id) as st (st.m)}<span class:good={st.ok} class:bad={!st.ok}>{st.m}: {st.ok ? st.why : st.why}</span>{/each}
          <button type="button" class="link" onclick={() => endpoints.splice(i, 1)}>remove</button>
        </div>
      </div>
    {/each}
    <div class="row"><button type="button" onclick={addEndpoint}>Add endpoint</button><span class="small muted">{epMsg}</span><button class="primary" onclick={saveEndpoints}>Save endpoints</button></div>

    <h2>Claude logins</h2>
    <p class="small muted">Every subscription can stay logged in at the same time: each lives in its own directory with its own credentials, and nothing logs the others out. "Log in" opens Claude for that login here: pick the login method, open the sign-in page, paste the code it shows into the box. Pressing it again returns to the same login instead of opening another. All slots share instructions, skills and transcripts, so a running session can move from one subscription to the other with "Switch agent / model", and the Limits page shows every plan you are logged into.</p>
    {#each Object.entries(cat?.machines ?? {}) as [m, c] (m)}
      {#if c.ok && c.harnesses?.claude}
        <div class="card">
          <div class="lead">{m}</div>
          {#each c.logins as l (l.dir)}
            <div class="lrow">
              <span><b>{l.name}</b> <span class="muted small">~/{l.dir}</span></span>
              <span class={l.logged_in ? 'good small' : 'bad small'}>{l.logged_in ? l.account : 'not logged in'}</span>
              <button type="button" onclick={() => openLogin(m, l.dir === '.claude' ? 'default' : l.name)}>{l.logged_in ? 'Log in again' : 'Log in'}</button>
            </div>
          {/each}
          <div class="lrow">
            <input placeholder="another subscription, e.g. personal" value={loginName[m] ?? ''} oninput={(e) => (loginName[m] = e.currentTarget.value.replace(/[^a-z0-9_-]/g, ''))} onkeydown={(e) => { if (e.key === 'Enter' && loginName[m]) openLogin(m, loginName[m]) }} />
            <button type="button" disabled={!loginName[m]} onclick={() => openLogin(m, loginName[m])}>Add and log in</button>
          </div>
          {#if loginMsg[m]}<p class="small bad inner">{loginMsg[m]}</p>{/if}
        </div>
      {/if}
    {/each}
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
  .hint { font-size: 11.5px; }
  .status { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 8px; align-items: center; }
  .good { color: var(--moss); }
  .bad { color: var(--signal); }
  .link { margin-left: auto; border: 0; background: none; color: var(--muted); font-size: 12.5px; padding: 0; text-decoration: underline; }
  .lrow { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between; padding: 7px 0; border-top: 1px solid var(--hairline); }
  .lrow input { flex: 1; min-width: 160px; padding: 5px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--page); font: inherit; font-size: 14px; }
  .lrow button { font-size: 13px; padding: 4px 10px; }
  h1 { font-size: 22px; margin: 16px 16px 8px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--hairline); }
  .lead { font-weight: 500; }
  .seg { display: flex; gap: 6px; }
  p { padding: 0 16px; }
</style>
