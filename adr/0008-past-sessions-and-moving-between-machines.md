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

## Revision: working-environment transfers (2026-09-19)

The original transcript-only implementation is insufficient for ongoing work. Moves
now request an environment bundle by default. It includes the project tree (Git data,
hidden and untracked files, local dependencies), harness working configuration, skills,
plugins, and Claude project memory. Harness credentials are excluded; the destination
login is linked into an isolated per-move configuration directory. Shared skills are
copied into the destination project's `.agents/skills`. Skill/plugin symlinks are
materialized; project symlinks remain links. Codex resume explicitly selects the new cwd.

The default UI destination is a separate project folder. Existing differing files cause
an error, not replacement. Destination executable/configuration and archive checks run
through `session.prepare` before the source is stopped. This is a separate protocol
operation so an older destination cannot mistake a preflight for a launch. After a
successful check, stopping moves take a fresh source snapshot, then import and launch.
Failures after the stop explicitly report that fact; the original files remain resumable.
This is a restart from persisted conversation state, not a live process migration or
an atomic transaction across hosts. Another process modifying either tree can still
cause the final import to fail. Environment data is limited to 32 GiB uncompressed.

Every resumed agent receives a relocation message naming both hosts and directories,
what was copied, and the need to identify missing folders/services. OS packages, running
commands, sockets, credentials outside the destination login, and dependencies outside
the copied roots are not reproduced. Virtualenv shebangs and absolute references may
need repair after relocation. The agent is explicitly told to check these before work.

Validation: `tests/test_workspace_transfer.py` covers real file bundles and node workflow
with mocked transport/launch for both harnesses. `tests/test_live_move.py` is an opt-in
native two-host test. Set `AGENTDASH_LIVE_MOVE_URL`, `AGENTDASH_WEB_TOKEN`,
`AGENTDASH_LIVE_MOVE_SOURCE`, and `AGENTDASH_LIVE_MOVE_TARGET`, then run:

```
.venv/bin/pytest -v tests/test_live_move.py
```

It creates disposable `/tmp/agentdash-move-smoke-*` projects and tests native conversation
recall, dirty Git state, hidden/untracked files, a local executable dependency and skills.
It spends model tokens, retains files/transcripts as evidence, and stops only its own test
sessions. Login/trust/tool-approval dialogs may require interaction in the terminal;
`AGENTDASH_LIVE_MOVE_PERMISSIONS` explicitly selects the test's permission setting.
Deploy the updated hub and both nodes before running them.

Transfers use bounded 4 MiB requests with retries and idempotent upload offsets.
The dashboard receives live stage changes and byte counts over its event stream;
upload/download bars show progress for that stage, while packing and restore stages
are indeterminate. Preflight and final snapshots are labelled separately.
`tests/test_transfer_http.py` verifies byte-for-byte recovery after a lost upload response,
HTTP range downloads, progress counts, and refusal of mismatched upload offsets.

## Revision: browsing the full archive

History queries now filter every saved Claude, Codex and pi session before applying
pagination. The running roster retains its own limits. Results include hub-assigned
titles and request descriptions; optional conversation search scans decoded transcript
text on each online host and returns an excerpt. It no longer depends on the separate
agentsview index for search coverage. Hosts report errors separately so a missing node
cannot silently look like an empty archive. The UI offers host filtering, older pages,
and explicit “Resume on <host>” actions. Another host opens the environment-transfer
form with that destination selected. Resuming Claude locally preserves its original
configuration directory, including isolated moved environments.

Regression tests cover old results beyond the roster limit, renamed titles, Unicode
conversation content, cross-host pagination and preserving the resume environment.
A deployed laptop check returned 50 results at offset 500 and local content matches;
the initial full content search took approximately 31 seconds. Content search remains
optional because it reads the archives rather than maintaining a second index.

Native Claude validation on lc-dell → ws succeeded after signing into the destination:
the resumed agent recalled a conversation-only code and verified dirty Git state,
hidden/untracked files, an executable dependency and a skill. The test exposed missing
Claude onboarding metadata, an expired destination login, and the need to persist the
new cwd separately from historical transcript records; these are now handled. The CLI's
own auth-status check rejects a logged-out destination during preflight. A second resume
from history verified the isolated configuration and destination cwd were preserved.

The deployed native Codex lc-dell → ws test also passed (898 seconds including both
full environment snapshots). It verified the original conversation-only code, the new
cwd, dirty Git state, hidden/untracked files, executable dependency and skill. Both
native checks used disposable fixtures and stopped their test agents afterward.
Desktop (1280 px) and mobile (390 px) browser checks passed for transfer progress,
laptop history pagination/search, explicit resume destination selection, and the
remote sign-in link opening in the client browser. Final automated suite: 188 passed,
2 opt-in live cases skipped in that run; frontend check/build succeeded with 14 warnings.


Large environments: the original 2 GiB limit rejected the real pangenome project
during preflight packing, before the source was stopped. The shared archive/relay
limit is now 32 GiB and each move stage has a two-hour request deadline. Source
packing, relay upload, destination download, staging and final writes check free
space, retaining a 512 MiB reserve. Final writes on the same filesystem are summed;
staging uses filesystem hard links to avoid expanding archived hard links prematurely.
A regression test packs and restores a real logical file larger than 2 GiB, and a
relay test uploads/downloads bytes across the 2 GiB offset boundary.

The move form explicitly asks whether to create a missing destination directory,
checked by default. `create_dir=false` rejects a missing folder during destination
preflight, before the source is stopped. Existing directories remain subject to the
same no-overwrite conflict checks; there is no overwrite option. Desktop and mobile
browser checks verified the default and opt-out request payload.

## Revision: a moved session's directory is the move record's, not the harness store's (2026-09-20)

Codex keeps the source machine's cwd in its thread row and rollout even after
`codex resume --cd <destination>`; Claude transcripts also record the source cwd. On ws
the moved pangenome session therefore reported `/home/leechuck/Public/software/pangenome`,
and switching it to Claude Code failed with `no such directory` although the process ran
in `/mnt/data1/DogoHLA`. Now every Claude/Codex import writes a move record
(`agentdash-move.json`, one entry per session, still readable by nodes that know the old
single-session form), the roster takes a running Codex process's real cwd, and history
and `session.switch` consult the record for both harnesses. Regression tests:
`test_moved_codex_history_uses_saved_destination`, `test_moved_codex_hands_over_in_its_new_directory`.
