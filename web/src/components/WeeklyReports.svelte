<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import type { WeeklyReports } from '../lib/types'
  import { renderMarkdown } from '../lib/markdown'
  import ReportQuestions from './ReportQuestions.svelte'
  let data = $state<WeeklyReports | null>(null)
  let week = $state('')
  let busy = $state(false)
  let error = $state('')
  let selected = $state('')
  const labels = { received: 'Received', awaiting: 'Awaiting', overdue: 'Overdue', exempt: 'Exempt', unknown: 'Unknown' }
  async function load(refresh = false) {
    busy = true
    error = ''
    selected = ''
    data = null
    try {
      const r = await api.paWeekly(week, refresh)
      if (!r.ok) error = r.error ?? 'Could not load reports.'
      else { data = r; week = r.week }
    } catch (e) { error = String(e) }
    finally { busy = false }
  }
  onMount(() => load())
</script>
<section>
  <h2>Weekly reports</h2>
  <p class="muted">Friday reports from students and staff. Open a person to read their report and ask questions using their org notes.</p>
  <div class="controls">
    <label>Week <input type="week" bind:value={week} disabled={busy} onchange={() => load()} /></label>
    <button disabled={busy} onclick={() => load(true)}>{busy ? 'Loading…' : 'Check mail for reports'}</button>
  </div>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if data}
    <p class="small muted">{data.checked_at ? `Last checked: ${new Date(data.checked_at).toLocaleString()}` : 'No reliable last-check time. Check mail to update statuses.'} · Due by end of Friday (Saudi time).</p>
    <div class="counts">
      {#each Object.entries(labels) as [status, label]}
        <span>{data.members.filter(m => m.status === status).length} {label.toLowerCase()}</span>
      {/each}
    </div>
    <p class="small muted">{data.warning}</p>
    {#each data.members as m (m.slug)}
      <article>
        <button class="person" aria-expanded={selected === m.slug} onclick={() => selected = selected === m.slug ? '' : m.slug}>
          <span><b>{m.name}</b><span class="small muted role">{m.role}</span></span>
          <span class:overdue={m.status === 'overdue'}>{labels[m.status]}{m.flags?.length ? ' · review flags' : ''}</span>
        </button>
        {#if selected === m.slug}
          <div class="body">
            {#if m.error}<p role="alert">Source unavailable: {m.error}</p>{/if}
            {#if m.date}<p class="small muted">Received {m.date}</p>{/if}
            {#if m.flags?.length}<p class="small">Checks to review: {m.flags.join('; ')}</p>{/if}
            {#if m.report}<div class="report">{@html renderMarkdown(m.report)}</div>
            {:else}<p>No report text available for this week.</p>{/if}
            {#each m.followups ?? [] as f}
              <details><summary>Related message · {f.date}</summary><p class="small">Message ID: {f.message_id}</p><div class="report">{@html renderMarkdown(f.body)}</div>
                {#each f.attachments as a}<p><b>{a.file}</b></p><div class="report">{@html renderMarkdown(a.text || 'No text extracted.')}</div>{/each}
              </details>
            {/each}
            {#key `${data.week}/${m.slug}`}<ReportQuestions topic="weekly" week={data.week} member={m.slug} />{/key}
          </div>
        {/if}
      </article>
    {/each}
  {/if}
</section>
<style>
  section { padding: 0 16px 32px; }
  h2 { font-size: 18px; margin-top: 20px; }
  .controls, .counts { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  input { background: var(--surface); color: var(--ink); border: 1px solid var(--hairline); padding: 6px; border-radius: var(--radius); }
  .counts { margin-top: 18px; font-size: 13px; }
  article { border: 1px solid var(--hairline); border-radius: var(--radius); margin-top: 8px; background: var(--surface); }
  .person { width: 100%; display: flex; justify-content: space-between; gap: 12px; text-align: left; border: 0; padding: 12px; background: transparent; }
  .role { display: block; }
  .body { padding: 0 12px 12px; }
  .report { overflow-wrap: anywhere; }
  .overdue, [role=alert] { color: var(--signal); }
  summary { cursor: pointer; }
</style>
