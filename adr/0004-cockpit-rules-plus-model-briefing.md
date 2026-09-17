# ADR 0004: Cockpit = deterministic rules plus a model briefing

Date: 2026-09-17. Status: accepted.

## Context

With ten or more sessions across three machines the roster alone does not say
where attention is needed, which limit will run out first, or which session
should be compacted or replaced. Robert asked for a "meta-agent" that reads the
whole fleet and recommends actions.

## Decision

Two layers, both in `agentdash/hub/cockpit.py`:

1. **Rules** (`analyse`): free, instant, testable. Inputs are what the hub
   already stores (sessions, machines, pending decisions, usage snapshots and
   their recent history). Nodes add two facts per session for this:
   `context_pct` (Claude: prompt size of the last main-thread call, window size
   from the statusline sidecar or a 200k/1M guess; Codex: `token_count` events)
   and `last_user`. Output is a ranked list of findings with one action each.
2. **Briefing** (`Briefer`): a model gets a compact, secret-free digest plus
   the rule findings and returns JSON (summary, one line per session,
   up to six suggestions with optional prompt text). The call runs on a node
   (`cockpit.brief` frame, `agentdash/node/briefing.py`) because API keys live
   on nodes and never on the hub. Any OpenAI-compatible endpoint works; the
   default is OpenRouter.

The cockpit never acts. Suggested prompts become a draft in the session's
composer (or a copy button where the dashboard cannot send). Briefings are
generated when the Cockpit page is opened and the fleet has changed, at most
once per `cockpit_min_interval`, or on the Refresh button; the hub does not
spend tokens while nobody looks.

## Consequences

- The page is useful with no model key at all (rules only).
- The digest (session names, directories, last request, last output line,
  limit percentages) leaves the tailnet for the model provider. No transcripts,
  tokens or file contents are included.
- One briefing costs about 2 US cents with the default model.
- Money (OpenRouter credit) is reported to the model in dollars, never as a
  percentage, because "82 % used" of a large balance is not a warning.
