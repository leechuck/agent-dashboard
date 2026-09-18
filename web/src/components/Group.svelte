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
  const labels = { on_track: 'On track', attention: 'Needs attention', unknown: 'Unknown' }
  const members = $derived((data?.members ?? []).filter(m => filter === 'all' || m.state === filter))
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
<section>
  <h2>Group</h2>
  <p class="muted">Model assessments of research and work progress from weekly reports and org notes. Each includes its evidence; report receipt is shown separately.</p>
  <div class="controls">
    <button disabled={loading || !!busy || all} onclick={load}>{loading ? 'Loading…' : 'Reload roster'}</button>
    <button disabled={!data || !!busy || all} onclick={reviewAll}>Review everyone</button>
    {#if all}<button onclick={() => stop = true} disabled={stop}>{stop ? 'Stopping after this review…' : 'Stop after current review'}</button>{/if}
    <a href="#/personal/weekly">Weekly reports →</a>
  </div>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if data}
    <div class="filters" aria-label="Filter group by progress">
      <button class:active={filter === 'all'} onclick={() => filter = 'all'}>Everyone · {data.members.length}</button>
      {#each Object.entries(labels) as [state,label]}<button class:active={filter === state} onclick={() => filter = state}>{label} · {data.members.filter(m => m.state === state).length}</button>{/each}
    </div>
    {#each members as m (m.slug)}
      <article>
        <button class="person" aria-expanded={selected === m.slug} onclick={() => selected = selected === m.slug ? '' : m.slug}>
          <span><b>{m.name}</b><span class="small muted role">{m.role}</span></span>
          <span class:attention={m.state === 'attention'} class:ontrack={m.state === 'on_track'}>{busy === m.slug ? 'Reviewing…' : labels[m.state]}</span>
        </button>
        <p class="reason">{m.stale ? 'Sources changed or the review is older than two weeks. Review again for current status.' : m.review?.reason ?? 'No progress assessment yet.'}</p>
        <p class="small muted receipt">Latest report check: {m.latest_report ? `${m.latest_report.week} · ${m.latest_report.status}${m.latest_report.checked_at ? ' · ' + new Date(m.latest_report.checked_at).toLocaleString() : ''}` : 'No report data available'}</p>
        {#if selected === m.slug}
          <div class="detail">
            {#if m.review}
              <p class="small muted">Reviewed {new Date(m.review.reviewed_at).toLocaleString()}{m.stale ? ' · previous assessment: ' + labels[m.review.state] : ''}</p>
              {#if m.stale}<p>{m.review.reason}</p>{/if}
              <ul>{#each m.review.evidence as source}<li>{source}</li>{/each}</ul>
            {/if}
            <p class="small muted">{m.org_source}{m.has_notes ? '' : ' · notes unavailable'}</p>
            <button disabled={!!busy || all} onclick={() => { error = ''; review(m.slug) }}>Review progress</button>
          </div>
        {/if}
      </article>
    {:else}<p>No people match this filter.</p>{/each}
  {/if}
</section>
<style>
  section { padding: 0 16px 32px; }
  h2 { font-size: 20px; }
  .controls, .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 16px 0; }
  .active { background: var(--cobalt-soft); color: var(--cobalt); }
  article { background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); margin: 10px 0; }
  .person { display: flex; justify-content: space-between; gap: 12px; text-align: left; width: 100%; border: 0; background: transparent; padding: 14px 14px 6px; }
  .role { display: block; }
  .reason, .receipt { margin: 6px 14px 12px; }
  .detail { padding: 0 14px 14px; overflow-wrap: anywhere; }
  .attention, [role=alert] { color: var(--signal); }
  .ontrack { color: var(--moss); }
</style>
