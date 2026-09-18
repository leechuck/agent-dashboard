<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import type { GroupRoster } from '../lib/types'
  let data = $state<GroupRoster | null>(null)
  let error = $state('')
  let loading = $state(false)
  let busy = $state('')
  let all = $state(false)
  let stop = $state(false)
  let selected = $state('')
  let filter = $state('all')
  const labels = { on_track: 'On track', watch: 'Watch', attention: 'Needs attention', unknown: 'Unknown' }
  const members = $derived((data?.members ?? []).filter(m => filter === 'all' || m.state === filter))
  function category(role: string) {
    if (/postdoc|scientist|manager|staff/i.test(role)) return 'Staff'
    if (/phd/i.test(role)) return 'PhD students'
    if (/msc/i.test(role)) return 'Master’s students'
    return 'Visiting / affiliated'
  }
  const groups = $derived(['Staff', 'PhD students', 'Master’s students', 'Visiting / affiliated']
    .map(name => ({ name, members: members.filter(m => category(m.role) === name) }))
    .filter(g => g.members.length))
  function summary(m: GroupRoster['members'][number]) {
    if (m.stale) return 'Assessment needs updating.'
    return m.review?.reason ?? 'No current assessment.'
  }
  async function load() {
    loading = true
    try { const r = await api.paGroup(); if (!r.ok) throw new Error(r.error ?? 'Could not load the group.'); data = r; error = '' }
    catch (e) { error = String(e) }
    finally { loading = false }
  }
  async function review(slug: string) {
    busy = slug
    try {
      const r = await api.paReviewMember(slug)
      if (!r.ok || !r.review) throw new Error(r.error ?? 'Review unavailable')
      const m = data?.members.find(m => m.slug === slug)
      if (m) { m.review = r.review; m.state = r.review.state; m.stale = false }
    } catch (e) { error = `${data?.members.find(m => m.slug === slug)?.name}: ${e}`; stop = true }
    finally { busy = '' }
  }
  async function reviewAll() {
    if (!data || busy) return
    all = true; stop = false; error = ''
    for (const m of data.members) { if (stop) break; await review(m.slug) }
    all = false
  }
  onMount(() => { void load(); return () => { stop = true } })
</script>
<section class="group-page">
  <div class="page-heading">
    <div><h2>Group</h2><p class="muted intro">Research progress, people and next steps.</p></div>
    <a href="#/personal/weekly">Weekly reports →</a>
  </div>
  <div class="controls">
    <button disabled={loading || !!busy || all} onclick={load}>{loading ? 'Loading…' : 'Reload'}</button>
    <button disabled={!data || !!busy || all} onclick={reviewAll}>Review everyone</button>
    {#if all}<button onclick={() => stop = true} disabled={stop}>{stop ? 'Stopping after this review…' : 'Stop after current review'}</button>{/if}
  </div>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if data}
    <div class="filters" aria-label="Filter group by progress">
      <button aria-pressed={filter === 'all'} onclick={() => filter = 'all'}>Everyone <span>{data.members.length}</span></button>
      {#each Object.entries(labels) as [state,label]}<button aria-pressed={filter === state} onclick={() => filter = state}>{label} <span>{data.members.filter(m => m.state === state).length}</span></button>{/each}
    </div>
    <p class="legend small muted"><b>Needs attention:</b> your intervention is needed now. <b>Watch:</b> a risk or follow-up to monitor. Expand a person for details.</p>
    {#each groups as g}
      <div class="cohort">
        <h3>{g.name} <span>{g.members.length}</span></h3>
        {#each g.members as m (m.slug)}
          <article class:expanded={selected === m.slug}>
            <button class="person" aria-expanded={selected === m.slug} onclick={() => selected = selected === m.slug ? '' : m.slug}>
              <span class="identity"><b>{m.name}</b><span class="role muted">{m.role}</span></span>
              <span class="status" data-state={m.state}>{busy === m.slug ? 'Reviewing…' : labels[m.state]}</span>
              <span class="summary">{summary(m)}</span>
              <span class="expand" aria-hidden="true">{selected === m.slug ? '−' : '+'}</span>
            </button>
            {#if selected === m.slug}
              <div class="detail">
                {#if m.stale}<p class="small muted">Sources, review criteria or freshness changed. The previous assessment below is out of date.</p>{/if}
                {#if m.review}
                  <div class="review-grid">
                    <div><h4>Progress</h4>{#if m.review.progress?.length}<ul>{#each m.review.progress as point}<li>{point}</li>{/each}</ul>{:else}<p class="muted">No recent progress recorded.</p>{/if}</div>
                    <div><h4>Risks / open questions</h4>{#if m.review.risks?.length}<ul>{#each m.review.risks as point}<li>{point}</li>{/each}</ul>{:else}<p class="muted">No specific risk identified.</p>{/if}</div>
                  </div>
                  {#if m.review.next_step}<div class="next"><h4>Next step</h4><p>{m.review.next_step}</p></div>{/if}
                  {#if m.review.intervention}<div class="next intervention"><h4>Your action</h4><p>{m.review.intervention}</p></div>{/if}
                  <details class="sources"><summary>Evidence & report history</summary>
                    <p class="small muted">Model assessment · {new Date(m.review.reviewed_at).toLocaleString()}</p>
                    {#if !m.review.version}<p>{m.review.reason}</p>{/if}
                    <ul>{#each m.review.evidence as source}<li>{source}</li>{/each}</ul>
                    <p class="small muted">{m.org_source}{m.has_notes ? '' : ' · notes unavailable'}</p>
                    <p class="small muted">Latest report check: {m.latest_report ? `${m.latest_report.week} · ${m.latest_report.status}${m.latest_report.checked_at ? ' · ' + new Date(m.latest_report.checked_at).toLocaleString() : ''}` : 'No report data available'}</p>
                  </details>
                {:else}<p class="muted">Review the available reports and notes to assess current progress.</p>{/if}
                <button class="review-button" disabled={!!busy || all} onclick={() => { error = ''; review(m.slug) }}>Review progress</button>
              </div>
            {/if}
          </article>
        {/each}
      </div>
    {:else}<p>No people match this filter.</p>{/each}
  {/if}
</section>
<style>
  .group-page { padding: 22px 24px 40px; max-width: 1280px; margin: 0 auto; }
  .page-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  h2 { font-size: 24px; margin: 0; letter-spacing: -.02em; }
  .intro { margin: 5px 0 0; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0; }
  .filters { display: flex; gap: 4px 12px; flex-wrap: wrap; border-bottom: 1px solid var(--hairline); }
  .filters button { background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: 10px 0; color: var(--muted); }
  .filters button[aria-pressed=true] { color: var(--cobalt); border-bottom-color: var(--cobalt); }
  .filters span { font-family: var(--mono); font-size: 12px; margin-left: 5px; }
  .legend { line-height: 1.6; margin: 10px 0 24px; max-width: 80ch; }
  h3 { font-size: 14px; font-weight: 600; margin: 22px 0 8px; }
  h3 span { color: var(--muted); font-weight: 400; margin-left: 8px; }
  article { background: var(--surface); border-top: 1px solid var(--hairline); }
  article:last-child { border-bottom: 1px solid var(--hairline); }
  .person { display: grid; grid-template-columns: minmax(140px, 1fr) 130px minmax(200px, 2fr) 20px; align-items: center; gap: 16px; text-align: left; width: 100%; border: 0; border-radius: 0; background: transparent; padding: 16px 12px; }
  .person:hover { background: var(--cobalt-soft); }
  .identity b { font-size: 14px; }
  .role { display: block; font-size: 12px; margin-top: 3px; }
  .summary { font-size: 13px; line-height: 1.5; color: var(--muted); }
  .status { font-size: 12px; font-weight: 600; }
  .status::before { content: '●'; margin-right: 6px; font-size: 8px; vertical-align: 1px; }
  .status[data-state=attention], [role=alert] { color: var(--signal); }
  .status[data-state=on_track] { color: var(--moss); }
  .status[data-state=watch] { color: var(--amber); }
  .status[data-state=unknown], .expand { color: var(--muted); }
  .expand { font-size: 20px; }
  .detail { padding: 0 16px 18px; overflow-wrap: anywhere; font-size: 14px; }
  .review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; border-top: 1px solid var(--hairline); padding-top: 16px; }
  h4 { margin: 0 0 8px; font-size: 13px; }
  ul { padding-left: 18px; margin: 0; }
  li { margin-bottom: 7px; line-height: 1.5; max-width: 70ch; }
  .detail p { margin: 0 0 12px; line-height: 1.5; max-width: 75ch; }
  .next { margin-top: 16px; padding-left: 12px; border-left: 2px solid var(--hairline); }
  .intervention { border-left-color: var(--signal); }
  .sources { margin: 18px 0; color: var(--muted); }
  .sources summary { cursor: pointer; margin-bottom: 12px; font-size: 12px; }
  .sources li { font-size: 12px; }
  .review-button { font-size: 12px; }
  @media (max-width: 1150px) {
    .person { grid-template-columns: minmax(0, 1fr) auto 16px; gap: 6px 12px; }
    .summary { grid-row: 2; grid-column: 1 / 3; }
    .expand { grid-column: 3; grid-row: 1 / 3; }
  }
  @media (max-width: 600px) {
    .group-page { padding: 18px 14px 32px; }
    .page-heading { align-items: start; }
    .page-heading a { font-size: 12px; }
    .review-grid { grid-template-columns: 1fr; gap: 14px; }
    .person { padding: 14px 6px; }
    .detail { padding: 0 6px 16px; }
    .status { font-size: 11px; }
  }
</style>
