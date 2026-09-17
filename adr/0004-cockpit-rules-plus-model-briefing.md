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
   (`cockpit.brief` frame, `agentdash/node/briefing.py`) because logins and API
   keys live on nodes and never on the hub. Default provider: one headless turn
   of the local Claude Code (`claude -p --model sonnet`, no tools, no user
   settings so no hooks fire, no MCP, no saved session, own working directory
   that the roster ignores) on the node's subscription login; the hub prefers
   the node named in `AGENTDASH_COCKPIT_NODE`. Alternative provider `openai`:
   any OpenAI-compatible endpoint with a key.

The cockpit never acts by itself. A suggestion can be carried out from the page
with one press (Send, Stop, Compact now, Allow/Deny), edited first, or copied.
Slash commands cannot go through Claude's inbox socket (they arrive as a peer
message, verified 2026-09-17); they are typed into the tmux pane instead, which
also gives Codex sessions in tmux a prompt channel. Briefings are
generated when the Cockpit page is opened and the fleet has changed, at most
once per `cockpit_min_interval`, or on the Refresh button; the hub does not
spend tokens while nobody looks.

## Consequences

- The page is useful with no model key at all (rules only).
- The digest (session names, directories, last request, last output line,
  limit percentages) leaves the tailnet for the model provider. No transcripts,
  tokens or file contents are included.
- One briefing is one Sonnet turn on the subscription (about 20 s); with the
  `openai` provider about 2 US cents.
- Money (OpenRouter credit) is reported to the model in dollars, never as a
  percentage, because "82 % used" of a large balance is not a warning.

## Amendment, 2026-09-17 (later the same day)

- The page is called Overview and the model part Advice; the code keeps `cockpit`.
- Advice is generated only when the owner presses the button. The timed refresh is gone.
- The agent is chosen on the Settings page and stored on the hub (`settings` table, key
  `agents`): harness `claude` (headless `claude -p`), `codex` (`codex exec --ephemeral
  -s read-only`) or `api`, plus model, thinking effort, machine and Claude login. The
  node's `.env` values are only defaults.
- The same call path names sessions (`Titler`): one small request (default Haiku) when a
  session is new or was asked something new, at most every 15 minutes, switchable off.
  Titles are stored on the hub and merged into the roster as `extra.title`.
