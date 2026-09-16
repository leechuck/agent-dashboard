# agentdash

Fleet dashboard for coding agents (Claude Code, Codex, pi, opencode) across
machines. One hub, one node per machine, a phone-first web app.

- history and search come from [agentsview](https://www.agentsview.io/)
- deep per-session interaction from the phone uses Claude Remote Control and
  Codex remote control; agentdash links into them
- agentdash itself owns the live roster, the decisions inbox, prompt sending,
  tmux terminals and rate-limit windows

See `adr/` for the reasoning and `docs/runbook.md` for setup.

## Development

```
just install      # uv sync + npm install
just dev          # hub on 127.0.0.1:8790
just node         # local node connecting to the hub
just web          # vite dev server with API proxy
just test
```
