# ADR 0008: Past sessions come from the machines; a session moves as a bundle through the hub

Date: 2026-09-17. Status: accepted.

## Context

The board shows what runs now. Everything that ran before was reachable only through
agentsview, which indexes one machine unless SSH sync is set up, names machines its own
way ("local") and cannot start anything. Robert wants to get back to any old session of
any agent, resume it, and carry a session from the laptop to the workstation and back.

Facts that shaped the design (verified 2026-09-17):

- Claude Code resumes from `<config>/projects/<slug(cwd)>/<id>.jsonl` and needs nothing
  else. A transcript copied to another machine, even into the folder of a different
  cwd, resumes with its memory intact (`--resume <id>` answered from the copied context).
  Sub-agent transcripts live next to it in `<id>/subagents/`.
- Codex resumes from the rollout file alone: `codex resume <id>` finds a rollout copied
  under `~/.codex/sessions/YYYY/MM/DD/` and indexes the thread in `state_5.sqlite` itself.
- pi resumes by file (`--session <path>`); its files live in
  `~/.pi/agent/sessions/-<cwd with / as ->--/`.
- Both machines can reach the hub over HTTP; they do not need to reach each other.

## Decision

1. **Past sessions are listed by the nodes**, from what each harness keeps on disk
   (`node/past.py`): Claude `projects/` (all logins share one folder through the symlink
   `agentdash install account` makes, so it is read once), the Codex thread table, pi's
   files. Newest first, named by the harness' own title when there is one (Claude writes
   an `ai-title` record into the transcript, Codex keeps `title`), else the first request.
   The hub fans `past.list` out to the online machines (`GET /api/past`) and remembers
   what came back, so a past session opens like a live one: same key scheme, same
   transcript view, the node reads the file when asked. agentsview stays as optional
   full-text search.
2. **Resume = switch on a session without a process.** `session.switch` already restarts a
   conversation with `--resume` under other settings; it now finds sessions on disk too,
   and skips the stop when there is nothing to stop.
3. **Moving is a bundle relayed by the hub.** `POST /api/sessions/{key}/move`:
   the source node ends the process (two agents must never write one transcript), packs
   the transcript (and the sub-agent folder) into a tar.gz and `PUT`s it to
   `/nodes/blob/<id>` with its node token; the target node `GET`s it, files it where its
   harness looks (`projects/<slug(new cwd)>/`, `sessions/<same relative path>/`, pi's
   folder for the new cwd) and starts the agent in tmux with `--resume`. The hub deletes
   the bundle when the move is over, whichever way it ended. Bundles live under
   `~/.agentdash/transfer` on the hub for seconds and are never stored in the database.
   The folder must exist on the target (the owner clones repositories himself); the
   transcript on the source stays, so a failed or regretted move loses nothing.
4. **An expired Claude login is shown, not guessed at.** The usage collector already calls
   Anthropic with each login's token; a 401/403 marks that login dead. The catalog shows
   it as not logged in, the hub pushes one notification, and Settings offers "Log in
   again" for the main `~/.claude` too (login slot `default`).

## Consequences

- History works for every machine without agentsview or SSH between machines.
- A move costs one process restart and a few seconds; the conversation's memory comes
  along. Codex sessions in a folder the target has never trusted ask the trust question
  in their terminal, as any fresh start there would.
- Moving to a different folder path works for Claude (the slug follows the new cwd) and
  pi; Codex keeps its own `cwd` in the rollout and simply runs where it is started.
- opencode and Hermes sessions are listed by the roster only and cannot move (no
  file-level resume); a handover briefing to another harness remains the way.
