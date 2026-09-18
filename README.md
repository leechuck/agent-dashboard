# agentdash

One dashboard for every coding-agent session Robert runs: Claude Code, Codex
CLI, pi, opencode, Hermes, and anything inside tmux, across the laptop, the
office workstation and leechuck.de. Phone first.

What it does:

- **Overview**: one screen that says where you are needed. Deterministic rules
  rank approvals, blocked sessions, limits about to run out (with burn rate and
  which harness still has room), context windows filling up, silent busy
  sessions, agents sharing a directory, and stale sessions. When you ask for
  it, a model adds advice: what each agent is doing and what to do next
  (answer, compact, hand off to a fresh session, fan out, switch harness),
  with prompts you can send with one tap, edit first, or copy. Which agent
  gives the advice (Claude Code, Codex or an API endpoint, model, machine) is
  set on the Settings page; it runs only on request (ADR 0004).
- **Personal**: a message briefing from mail, Mattermost and WhatsApp, with
  source links, follow-up questions and folded action details. Separate **Weekly
  reports**, **Tasks** and **Calendar** tabs keep the default view focused. Weekly
  reports show receipt per group member, check times and review flags; expand a
  report to ask questions grounded in it and the person's org notes. Mail checks
  run only on request. Draft sending still goes through Gnus (ADR 0005).
- **Fleet**: a board of large cards, one per agent, titled by a small model
  after what the agent is actually working on; opening one slides the session
  in, the board folds into a side rail, and a chip bar or the arrow keys hop
  between sessions. Per machine, status (busy, idle, waiting for you),
  model, login, thinking depth, context fill, last line. Sub-agents (Claude or
  Codex sessions started by another session) fold under their parent.
- **Decisions**: permission prompts from Claude (and Codex, pi) answered from
  the phone while a machine is *armed*; otherwise the terminal dialog appears
  as usual (ADR 0002).
- **New session / Switch**: start any installed harness (Claude Code, Codex, pi,
  opencode) on any Claude login or any endpoint you configured (OpenRouter, your
  own vLLM server, ...), with model and thinking level from lists; move a running
  session to another subscription, model or harness (ADR 0006). Second Claude
  subscription: Settings, Claude logins, "Add and log in".
- **Session**: live transcript rendered like the agent's own terminal (Markdown,
  tool calls, diffs), rename or regenerate its title, send a message into a running session (Claude
  inbox socket, pi extension), stop/remove/respawn/fork background sessions,
  start new `claude --bg` sessions on any machine.
- **Terminal**: attach read-only (or take control) to any tmux pane.
- **Limits**: Claude session/week windows per login (several subscriptions
  side by side: `agentdash install account team`, then `claude-team` and
  `/login`; the cockpit says which login should take new work), Codex windows, OpenRouter credits
  and key cap, with reset countdowns and push at 80/95 %.
- **History**: every past session of every machine, read from what the
  harnesses keep on disk (Claude `projects/`, Codex threads, pi files), named by
  their own titles. Open one, resume it in tmux where it is, or **move it to
  another machine**: the transcript travels through the hub and the same
  conversation continues there with `--resume` (ADR 0008). agentsview adds
  full-text search when configured.

What it reuses: [agentsview](https://www.agentsview.io/) for history and
analytics; Claude Remote Control and Codex remote control for deep interaction
from the phone; the harnesses' own hooks, sockets and state stores instead of
wrapping any binary (ADR 0001).

## Start agents inside tmux

Claude Code and pi accept prompts from the dashboard wherever they run. Codex,
opencode, Hermes and anything else can only be typed into, and slash commands
(`/compact`) work only when typed, for every harness. `agentdash install
launcher` installs `agent-tmux`; `alias codex='agent-tmux codex'` (same for
`claude`, `pi`) gives every new agent its own tmux session, which also makes
its terminal available in the dashboard. For Codex, the shell function in
`agentdash/install/files/codex-tmux.bashrc` is the better wrapper: it sends only
interactive runs into tmux and leaves `codex exec`, `app-server`, pipes and
scripts alone.

## Layout

```
agentdash/          Python package: hub (FastAPI), node (collectors, hooks,
                    terminals), hooks (stdlib entry points), installers, doctor
web/                Svelte 5 + Vite app, built into agentdash/static
pi-extension/       agentdash.ts, installed into ~/.pi/agent/extensions
deploy/             deploy.sh, systemd user units, cutover and mirror scripts
docs/runbook.md     where things run and how to operate them
adr/                decisions
```

## Development

```
just install          # uv sync + npm install
just test             # pytest
just dev              # hub on 127.0.0.1:8790 (dev db)
just node             # node against that hub
cd web && npm run build
deploy/deploy.sh <host> hub|node
uv run agentdash doctor
```

Secrets live in `~/.agentdash/.env` on each machine (`AGENTDASH_NODE_TOKEN`,
`AGENTDASH_WEB_TOKEN`, `OPENROUTER_API_KEY`, hub URL). Never in the repo.
