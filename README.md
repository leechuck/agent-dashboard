# agentdash

One dashboard for every coding-agent session Robert runs: Claude Code, Codex
CLI, pi, opencode, Hermes, and anything inside tmux, across the laptop, the
office workstation and leechuck.de. Phone first.

What it does:

- **Cockpit**: one screen that says where you are needed. Deterministic rules
  rank approvals, blocked sessions, limits about to run out (with burn rate and
  which harness still has room), context windows filling up, silent busy
  sessions, agents sharing a directory, and stale sessions. On top of that a
  model writes a short briefing: what each agent is doing and what to do next
  (answer, compact, hand off to a fresh session, fan out, switch harness),
  with prompts you can send with one tap, edit first, or copy. The model is the
  local `claude -p` on Sonnet using the subscription login of one node; no API
  key is needed. Nothing is sent until you press Send (ADR 0004).
- **Fleet**: live roster per machine, status (busy, idle, waiting for you),
  model, login, thinking depth, context fill, last line. Sub-agents (Claude or
  Codex sessions started by another session) fold under their parent.
- **Decisions**: permission prompts from Claude (and Codex, pi) answered from
  the phone while a machine is *armed*; otherwise the terminal dialog appears
  as usual (ADR 0002).
- **Session**: live transcript, send a message into a running session (Claude
  inbox socket, pi extension), stop/remove/respawn/fork background sessions,
  start new `claude --bg` sessions on any machine.
- **Terminal**: attach read-only (or take control) to any tmux pane.
- **Limits**: Claude session/week windows per login (several subscriptions
  side by side: `agentdash install account team`, then `claude-team` and
  `/login`; the cockpit says which login should take new work), Codex windows, OpenRouter credits
  and key cap, with reset countdowns and push at 80/95 %.
- **History**: search every past session (agentsview) and resume it.

What it reuses: [agentsview](https://www.agentsview.io/) for history and
analytics; Claude Remote Control and Codex remote control for deep interaction
from the phone; the harnesses' own hooks, sockets and state stores instead of
wrapping any binary (ADR 0001).

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
