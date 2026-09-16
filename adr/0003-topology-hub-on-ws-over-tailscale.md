# ADR 0003: One hub on ws, one node per machine, reached over Tailscale

Date: 2026-09-16. Status: accepted.

## Context

ws is the always-on orchestration host (borg-cube). The laptop sleeps and
roams. The phone must reach the dashboard from anywhere and receive push
notifications, which requires an HTTPS origin.

## Decision

- The hub (FastAPI + SQLite + Web Push) runs on ws as a user systemd service,
  bound to loopback and published with `tailscale serve`, which provides a
  valid certificate under the tailnet name.
- Every machine runs `agentdash node`, which connects outbound to the hub over
  a websocket with a shared token. Nodes never listen on the network; their
  hook receiver binds to 127.0.0.1 only.
- Tokens (hub token, OpenRouter key) live in `~/.agentdash/.env`, never in the
  repository, in line with the tokens-over-passwords rule.

## Consequences

- A laptop that is asleep shows as offline with its last roster kept; sessions
  are marked offline, not deleted.
- The hub holds only session metadata, decision records and usage snapshots.
  Transcripts are streamed on demand from the owning node and cached in memory.
- Tailscale must be installed on laptop, ws and phone before Phase 3.
