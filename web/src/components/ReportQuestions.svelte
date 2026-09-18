<script lang="ts">
  import { api } from '../lib/api'
  import { renderMarkdown } from '../lib/markdown'
  let { topic = 'briefing', week = '', member = '' }: { topic?: string; week?: string; member?: string } = $props()
  let question = $state('')
  let busy = $state(false)
  let error = $state('')
  let answers = $state<{ question: string; text: string }[]>([])
  async function ask() {
    const q = question.trim()
    if (!q || busy) return
    busy = true
    error = ''
    try {
      const r = await api.paAsk(q, topic, week, member, answers.slice(-8))
      if (!r.ok) error = r.error ?? 'Could not answer this question.'
      else { answers = [...answers, { question: q, text: r.text ?? '' }]; question = '' }
    } catch (e) { error = String(e) }
    finally { busy = false }
  }
</script>
<div class="questions">
  {#each answers as a}
    <p><b>{a.question}</b></p>
    <div class="answer">{@html renderMarkdown(a.text)}</div>
  {/each}
  <form onsubmit={(e) => { e.preventDefault(); ask() }}>
    <label>Ask about {topic === 'weekly' ? 'this person’s report and org notes' : 'this briefing'}
      <textarea bind:value={question} maxlength="4000" rows="2" placeholder="What changed, and what needs closer attention?" disabled={busy}></textarea>
    </label>
    <button disabled={busy || !question.trim()}>{busy ? 'Reading the sources…' : 'Ask'}</button>
  </form>
  {#if error}<p role="alert">{error}</p>{/if}
</div>
<style>
  .questions { margin: 18px 0; border-top: 1px solid var(--hairline); padding-top: 12px; }
  label { display: grid; gap: 6px; font-size: 13px; }
  textarea { width: 100%; padding: 10px; background: var(--surface); color: var(--ink); border: 1px solid var(--hairline); border-radius: var(--radius); font: inherit; resize: vertical; }
  button { margin-top: 8px; }
  .answer { overflow-wrap: anywhere; }
  [role=alert] { color: var(--signal); }
</style>
