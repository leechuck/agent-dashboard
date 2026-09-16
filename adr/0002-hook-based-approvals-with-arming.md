# ADR 0002: Answer permission prompts from the phone through hooks, only while a machine is armed

Date: 2026-09-16. Status: accepted.

## Context

Claude Code (and Codex) raise permission prompts in the terminal. Claude's
`PermissionRequest` hook can decide `allow` or `deny` before the dialog
appears, which lets an external program answer from a phone without
wrapping the agent. The catch: while the hook runs, the terminal shows
nothing. A hook that always waited for the phone would freeze the terminal
for anyone sitting at it.

## Decision

- The node forwards a prompt to the hub only while the machine is **armed**:
  a toggle in the dashboard (default 12 hours, then it disarms itself) or the
  presence of `~/.agentdash/away`. Unarmed, the hook returns at once with no
  decision and the normal dialog shows.
- Armed, the hook posts to the node over loopback and waits up to
  `decision_timeout` (1770 s, below the 1800 s hook timeout). The answer from
  the phone becomes the hook's decision. On timeout the hook returns nothing
  and the terminal dialog appears.
- `AskUserQuestion` cannot be answered by a hook; the node records it as a
  question, pushes a notification with the Remote Control deep link, and lets
  the dialog show.
- "Allow X for this session" is remembered on the node per (session, tool),
  never for `Bash`.
- Hook entry points are standard-library Python (`agentdash.hooks.claude`)
  and fail silent: a dead node must never break a session.

## Consequences

- No agent binary is wrapped; existing terminal habits stay intact.
- Approvals for `claude -p` runs are out of scope (hook does not fire there).
- The hub stores every decision with its outcome, giving an audit trail.
