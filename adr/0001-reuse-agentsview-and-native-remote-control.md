# ADR 0001: Reuse agentsview for history and the harnesses' own remote control for deep interaction

Date: 2026-09-16. Status: accepted.

## Context

Robert wants one dashboard over all coding-agent sessions (Claude Code, Codex,
pi, opencode, Claude via OpenRouter) on the laptop and on ws, reachable from an
Android phone, with a decisions inbox, history and rate-limit windows.

Existing tools were evaluated (see the plan in `~/.claude/plans/`):
Happy (Claude/Codex/Gemini only, must launch through it), Vicoa (three weeks
old, AGPL, store apps cannot reach a self-hosted backend), pi-web (pi only),
tmux managers (no decisions, no limits), OpenClaw (heavy).

Already on the machine: agentsview 0.33.1, which indexes every harness into
SQLite with full-text search, an HTTP API, SSE and SSH sync from remote
hosts. It is read-only.

## Decision

- agentsview stays the history and search layer. agentdash proxies its API and
  deep-links to its UI; it does not re-implement transcript search or cost
  analytics.
- Claude Remote Control and Codex remote control (ChatGPT app pairing) are the
  deep-interaction layer: full chat, AskUserQuestion, images. agentdash stores
  the bridge id from hooks and deep-links to it.
- agentdash owns what nothing else does: the cross-machine live roster, the
  decisions inbox for terminal-started sessions, prompt injection, tmux web
  terminals, rate-limit windows across providers, push notifications.

## Consequences

- No wrapper around the agent binaries; sessions are observed through the
  harnesses' own registries, transcripts, hooks and sockets.
- agentdash depends on semi-internal interfaces (`claude agents --json`, the
  inbox socket, Codex state db). They are isolated in `node/adapters` and
  pinned to tested versions in the README.
- Vendor relays carry the deep-interaction traffic; the hub itself never
  leaves the tailnet.
