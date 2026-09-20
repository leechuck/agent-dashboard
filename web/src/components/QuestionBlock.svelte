<script lang="ts">
  import type { AgentQuestion } from '../lib/questions'
  let { questions, answerHref = '', external = false }: { questions: AgentQuestion[]; answerHref?: string; external?: boolean } = $props()
</script>

<section class="question-block" aria-label="Agent question">
  <div class="question-heading"><span class="question-mark" aria-hidden="true">?</span><strong>Question from the agent</strong></div>
  {#each questions as q}
    <div class="question-body">
      {#if q.title}<div class="topic">{q.title}</div>{/if}
      <h3>{q.question}</h3>
      {#if q.options.length}
        <p class="option-hint">{q.multiple ? 'Multiple choices allowed' : 'Options'}</p>
        <ol class="options">
          {#each q.options as option}
            <li><strong>{option.label}</strong>{#if option.description}<p>{option.description}</p>{/if}</li>
          {/each}
        </ol>
      {/if}
    </div>
  {/each}
  {#if answerHref}
    <a class="answer" href={answerHref} target={external ? '_blank' : undefined} rel={external ? 'noopener' : undefined}>{external ? 'Answer in the agent app' : 'Answer in terminal'}</a>
  {/if}
</section>

<style>
  .question-block { margin: 10px 0; padding: 14px 16px; border: 1px solid var(--amber); border-left-width: 4px; border-radius: var(--radius); background: var(--surface); overflow-wrap: anywhere; scroll-margin-top: 150px; }
  .question-heading { display: flex; align-items: center; gap: 9px; color: var(--amber); font-size: 14px; }
  .question-mark { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 50%; background: var(--amber-soft); font-weight: 600; flex: none; }
  .question-body + .question-body { border-top: 1px solid var(--hairline); margin-top: 16px; padding-top: 14px; }
  .topic { margin-top: 12px; font-size: 12px; color: var(--muted); }
  h3 { margin: 10px 0; font-size: 17px; line-height: 1.45; font-weight: 600; white-space: pre-wrap; }
  .option-hint { margin: 10px 0 6px; color: var(--muted); font-size: 12px; }
  .options { list-style: decimal; margin: 0; padding-left: 26px; display: grid; gap: 8px; }
  li { padding: 10px 12px; border: 1px solid var(--hairline); border-radius: 4px; background: var(--page); min-height: 44px; }
  li::marker { color: var(--amber); font-family: var(--mono); }
  li strong { font-size: 14px; }
  li p { margin: 4px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
  .answer { display: inline-flex; align-items: center; min-height: 44px; margin-top: 14px; padding: 8px 14px; background: var(--amber-soft); border: 1px solid var(--amber); border-radius: var(--radius); color: var(--amber); font-weight: 500; }
  @media (max-width: 600px) { .question-block { padding: 12px; } h3 { font-size: 16px; } .answer { width: 100%; justify-content: center; } }
</style>
