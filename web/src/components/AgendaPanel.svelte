<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import type { PAAgendaPanel, PAEvent } from '../lib/types'
  import PaPanel from './PaPanel.svelte'

  // declared first: the state below is initialised with helpers that use it
  const pad = (n: number) => String(n).padStart(2, '0')

  let data = $state<PAAgendaPanel | null>(null)
  let loading = $state(true)
  let error = $state('')
  let busy = $state(false)
  let msg = $state<{ ok: boolean; text: string } | null>(null)
  let adding = $state(false)
  // quick add
  let title = $state('')
  let kind = $state<'event' | 'reminder' | 'allday'>('event')
  let at = $state(nextHour())
  let day = $state(localDate(new Date()))
  let lastDay = $state('')
  let minutes = $state(30)
  let calendar = $state('work')

  function localDate(d: Date): string {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  }
  function nextHour(): string {
    const d = new Date(Date.now() + 3600000)
    return `${localDate(d)}T${pad(d.getHours())}:00`
  }
  /** "2026-09-18T15:00" as typed here, with this browser's offset on that day. */
  function rfc3339(local: string): string {
    const d = new Date(local)
    const off = -d.getTimezoneOffset()
    const sign = off < 0 ? '-' : '+'
    return `${local.length === 16 ? local + ':00' : local}${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`
  }
  const hm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  const riyadh = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Riyadh' })
  const away = $derived(new Date().getTimezoneOffset() !== -180)

  interface Day { date: string; label: string; events: PAEvent[] }
  const days = $derived.by(() => {
    const out = new Map<string, Day>()
    const today = localDate(new Date())
    const put = (date: string, e: PAEvent) => {
      if (date < today) return
      if (!out.has(date)) {
        const d = new Date(date + 'T12:00')
        const diff = Math.round((d.getTime() - new Date(today + 'T12:00').getTime()) / 86400000)
        const label = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })
        out.set(date, { date, label, events: [] })
      }
      out.get(date)!.events.push(e)
    }
    for (const e of data?.events ?? []) {
      if (!e.all_day) put(localDate(new Date(e.start)), e)
      else {
        // end date is exclusive
        const d = new Date(e.start + 'T12:00')
        const end = e.end || e.start
        for (let n = 0; n < 14 && (localDate(d) < end || n === 0); n++, d.setDate(d.getDate() + 1)) put(localDate(d), e)
      }
    }
    if (!out.has(today)) out.set(today, { date: today, label: 'Today', events: [] })
    return [...out.values()].sort((a, b) => a.date.localeCompare(b.date)).map((d) => ({ ...d, events: d.events.sort((x, y) => Number(y.all_day) - Number(x.all_day) || x.start.localeCompare(y.start)) }))
  })
  let showWeek = $state(false)
  const later = $derived.by(() => {
    const rest = days.slice(2).flatMap((d) => d.events)
    return { events: rest.length, flagged: new Set(rest.filter((e) => e.flags.length).map((e) => e.id + e.start)).size }
  })
  const trip = $derived.by(() => {
    const today = localDate(new Date())
    return (data?.trips ?? []).find((t) => t.start <= today && today < (t.end || t.start)) ?? null
  })
  const nextTrip = $derived.by(() => {
    const today = localDate(new Date())
    return (data?.trips ?? []).filter((t) => t.start > today).sort((a, b) => a.start.localeCompare(b.start))[0] ?? null
  })
  function lastDayOf(end: string): string {
    const d = new Date(end + 'T12:00')
    d.setDate(d.getDate() - 1)
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  }
  const summary = $derived.by(() => {
    if (!data) return ''
    const now = Date.now()
    const today = days.find((d) => d.label === 'Today')
    const timed = (today?.events ?? []).filter((e) => !e.all_day)
    const next = (data.events ?? []).find((e) => !e.all_day && new Date(e.end || e.start).getTime() > now)
    const parts = [`${timed.length} today`]
    if (next) parts.push(`next ${hm(next.start)} ${next.title}`)
    if (data.flagged) parts.push(`${data.flagged} to check`)
    return parts.join(' · ')
  })

  async function load(fresh = false) {
    loading = true
    try {
      const r = await api.paPanel<PAAgendaPanel>('agenda', fresh)
      if (r.ok) {
        data = r
        error = r.warning ?? ''
      } else error = r.error ?? 'failed'
    } catch (e) {
      error = String(e)
    } finally {
      loading = false
    }
  }
  onMount(() => {
    load()
    const t = setInterval(() => document.visibilityState === 'visible' && load(), 600000)
    return () => clearInterval(t)
  })

  async function add() {
    if (!title.trim()) return
    busy = true
    msg = null
    try {
      const r =
        kind === 'reminder'
          ? await api.paAct('calendar_remind', { title, start: rfc3339(at), calendar })
          : kind === 'allday'
            ? await api.paAct('calendar_add', { title, start: day, end: lastDay || '', all_day: true, calendar })
            : await api.paAct('calendar_add', { title, start: rfc3339(at), minutes, calendar })
      msg = r.ok ? { ok: true, text: kind === 'reminder' ? 'Reminder set; your phone will ring.' : 'On the calendar.' } : { ok: false, text: r.error ?? 'failed' }
      if (r.ok) {
        title = ''
        await load(true)
      }
    } catch (e) {
      msg = { ok: false, text: String(e) }
    } finally {
      busy = false
    }
  }
</script>

<PaPanel id="agenda" title="Calendar" {summary} alert={(data?.flagged ?? 0) > 0} {loading} {error} onrefresh={() => load(true)}>
  {#if trip}<p class="trip">{trip.title}{#if !/\b(back|until)\b/i.test(trip.title)}, until {lastDayOf(trip.end)}{/if}{#if away} · times here are local, Riyadh time in brackets{/if}</p>
  {:else if nextTrip}<p class="trip next">Next: {nextTrip.title}, from {new Date(nextTrip.start + 'T12:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</p>{/if}

  {#if data}
    {#each showWeek ? days : days.slice(0, 2) as d (d.date)}
      <h3>{d.label}</h3>
      <ul>
        {#each d.events as e (e.id + e.start)}
          <li class:flagged={e.flags.length > 0} class:free={!e.busy && !e.all_day}>
            <span class="time">{#if e.all_day}all day{:else}{hm(e.start)}{#if e.end}<span class="to">–{hm(e.end)}</span>{/if}{/if}</span>
            <span class="what">
              {#if e.link}<a href={e.link} target="_blank" rel="noopener">{e.title}</a>{:else}{e.title}{/if}
              {#if away && !e.all_day}<span class="small muted"> ({riyadh(e.start)})</span>{/if}
              {#if e.unanswered}<span class="tag">invitation open</span>{/if}
              {#each e.flags as f (f)}<span class="flag">{f}</span>{/each}
              {#if e.location}<span class="loc small muted">{e.location}</span>{/if}
            </span>
          </li>
        {/each}
        {#if d.events.length === 0}<li class="none small muted">Nothing on the calendar.</li>{/if}
      </ul>
    {/each}
    {#if days.length > 2}
      <button class="ghost small" onclick={() => (showWeek = !showWeek)}>
        {showWeek ? 'Only today and tomorrow' : `Rest of the week: ${later.events} events${later.flagged ? `, ${later.flagged} to check` : ''}`}
      </button>
    {/if}
  {:else if loading}
    <p class="small muted">Asking the laptop…</p>
  {/if}

  {#if adding}
    <form class="add" onsubmit={(ev) => { ev.preventDefault(); add() }}>
      <input class="text" bind:value={title} placeholder={kind === 'reminder' ? 'Remind me to…' : 'What'} maxlength="200" />
      <div class="addrow">
        <select bind:value={kind} aria-label="kind">
          <option value="event">Event</option>
          <option value="reminder">Reminder</option>
          <option value="allday">All day / trip</option>
        </select>
        {#if kind === 'allday'}
          <input type="date" bind:value={day} aria-label="first day" />
          <input type="date" bind:value={lastDay} min={day} aria-label="last day (optional)" />
        {:else}
          <input type="datetime-local" bind:value={at} aria-label="when" />
        {/if}
        {#if kind === 'event'}
          <select bind:value={minutes} aria-label="length">
            <option value={15}>15 min</option><option value={30}>30 min</option><option value={60}>1 h</option><option value={90}>1.5 h</option><option value={120}>2 h</option>
          </select>
        {/if}
        <select bind:value={calendar} aria-label="calendar">
          {#each data?.calendars ?? ['work', 'personal'] as c (c)}<option value={c}>{c}</option>{/each}
        </select>
        <button class="primary" disabled={busy || !title.trim()}>{busy ? 'Adding…' : 'Add'}</button>
      </div>
      <p class="small muted">Goes on your own calendar, without guests. {kind === 'reminder' ? 'A reminder rings at that time.' : ''}</p>
    </form>
  {:else}
    <button class="ghost small" onclick={() => (adding = true)}>+ event or reminder</button>
  {/if}
  {#if msg}<p class={`small ${msg.ok ? 'okmsg' : 'err'}`}>{msg.text}</p>{/if}
</PaPanel>

<style>
  .trip { margin: 0 0 6px; padding: 5px 10px; border-radius: var(--radius); background: var(--cobalt-soft); color: var(--cobalt); font-size: 13px; }
  .trip.next { background: none; padding: 0; color: var(--muted); }
  h3 { margin: 10px 0 2px; font-size: 13px; font-weight: 600; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; gap: 10px; padding: 5px 0; border-bottom: 1px solid var(--hairline); }
  li.free { opacity: .7; }
  .time { flex: none; width: 92px; font-family: var(--mono); font-size: 12.5px; color: var(--muted); padding-top: 1px; }
  .to { opacity: .7; }
  .what { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .what a { color: inherit; }
  .flag, .tag { display: inline-block; margin-left: 6px; font-size: 11.5px; font-weight: 600; padding: 0 6px; border-radius: 8px; color: var(--signal); background: var(--signal-soft); }
  .tag { color: var(--amber); background: var(--amber-soft); }
  .loc { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .none { border: 0; }
  .add { display: grid; gap: 6px; margin-top: 10px; }
  .text, .addrow input, .addrow select { padding: 6px 8px; border: 1px solid var(--hairline); border-radius: var(--radius); background: var(--surface); color: inherit; font: inherit; font-size: 14px; }
  .text { width: 100%; }
  .addrow { display: flex; flex-wrap: wrap; gap: 6px; }
  .addrow button { font-size: 13px; }
  .add p { margin: 0; }
  .ghost { border-color: transparent; background: none; color: var(--muted); padding: 2px 8px; margin-top: 6px; }
  .ghost:hover { color: var(--ink); border-color: var(--hairline); }
  .err { color: var(--signal); }
  .okmsg { color: var(--moss); }
  @media (max-width: 600px) { .time { width: 78px; } }
</style>
