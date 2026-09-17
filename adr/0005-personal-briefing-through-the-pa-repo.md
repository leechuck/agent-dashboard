# ADR 0005: Personal briefing runs in the PA repo; mail leaves only through Emacs

Date: 2026-09-17. Status: accepted.

## Context

Robert starts most days by asking an agent in `~/pa` (personal-assistant skill) to read
mail, Mattermost and WhatsApp, draft replies and list what he owes. He wants that in the
cockpit, with two additions: approve replies from the dashboard, and hand work that comes
out of the briefing to other agents, in the folder where that kind of work lives.
Constraints from the PA repo: mail is composed and sent only through Gnus in the running
Emacs; nothing is sent without his explicit authorization; WhatsApp bodies never leave
the laptop's gitignored files.

## Decision

- The briefing is an ordinary Claude background session (`pa-briefing`) started in the PA
  repo with a fixed prompt (`agentdash/node/pa_prompt.md`). It behaves as the skill says
  and additionally writes `data/dashboard_briefing.json`: items with an optional `draft`
  (Gnus buffer name, message-id, body) and an optional `task` (self-contained prompt,
  workspace, machine, path).
- `configs/workspaces.yaml` in the PA repo lists where each kind of work runs. The agent
  routes tasks with it; the dashboard offers it as targets for a new agent.
- The node (`agentdash/node/pa.py`) owns everything personal. The hub only relays
  `pa.request` frames and stores nothing; item state lives in `~/.agentdash/pa-state.json`.
- "Send via Emacs" is the one sending path: `emacsclient -s gnus` calls
  `claude-email-send-buffer` on the recorded draft. An edited body discards the draft with
  the standard `message-kill-buffer` and re-creates the reply with `claude-email-reply`
  before sending. Recipients always come from the briefing file, never from the request.
  A draft that is still open after the send call is reported as not sent.
- Tasks go to a new `claude --bg` session in the chosen workspace (any machine) or as a
  prompt to a running session; the item is then marked delegated.
- Mattermost and WhatsApp replies have no direct path. The dashboard asks the
  `pa-briefing` session to send them, quoting the exact text as the authorization.

## Consequences

- The briefing appears only while the laptop node is online.
- A follow-up reaches the PA session as a peer message, which Claude Code treats with
  suspicion by design; the PA repo's CLAUDE.md says which authorizations are valid, but
  the agent may still ask Robert in the terminal.
- The model writes the JSON, so the node normalises it and the UI tolerates gaps.
