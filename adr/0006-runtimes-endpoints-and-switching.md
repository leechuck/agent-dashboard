# ADR 0006: Any harness, any login, any endpoint; switching is restart-with-resume

Date: 2026-09-17. Status: accepted.

## Context

The dashboard could only start `claude --bg` on the default login. Robert runs four
harnesses, two Claude subscriptions, OpenRouter, and his own Qwen server on unimatrix01,
and wants to choose among them when starting work, in the dashboard's own features, and
for sessions that are already running (a limit is near, a model is wrong for the task).

## Decision

**A runtime is harness x source x model.** Source is one of: the machine's default, a
Claude login (a config directory, ADR-less detail in `install/account.py`), or an
*endpoint*.

**Endpoints are configuration, keys are not.** An endpoint record (hub `settings` table,
key `endpoints`, editable on the Settings page) holds an id, an OpenAI-compatible address,
optionally an Anthropic-compatible address (what Claude Code needs), what Codex should
speak (`chat`/`responses`), a context window, and the *name* of the environment variable
that holds the key. The key lives in `~/.agentdash/.env` on each machine that should use
the endpoint and is read there at launch. The hub sends the records with every request
that needs them; it never sees a key. OpenRouter and the BORG Qwen server are seeded.

**The node turns a runtime into a process** (`node/launcher.py`):

| harness | login | endpoint |
|---|---|---|
| claude | `CLAUDE_CONFIG_DIR` | `ANTHROPIC_BASE_URL`/`AUTH_TOKEN`/model variables, own config dir `~/.claude-<id>` sharing instructions and transcripts |
| codex | - | `-c model_provider=... -c model_providers.<id>.*`, key variable in the environment |
| pi | - | provider entry added to `~/.pi/agent/models.json`, `--model <id>/<model>` |
| opencode | - | not supported (own config file) |

Everything interactive starts in a detached tmux session (ADR 0004 amendment: tmux is the
one channel for prompts, slash commands and the web terminal); Claude may instead run as a
background job. The environment, which can contain a key, goes through a 0600 file that
the wrapper shell sources and deletes before `exec`; nothing secret is on a command line.

**The catalog** (`node/catalog.py`, `GET /api/catalog`) says per machine what is
installed, which Claude logins exist and are logged in, which models each harness offers
(Codex: its model cache; pi and opencode: their own listings; endpoints: `GET /models`),
and for each endpoint whether the key is present and the server reachable *from that
machine* (the Qwen server is campus-only, so this differs per machine). Every model field
in the UI is a list built from it, with "other" as the escape hatch.

**Switching** (`POST /api/sessions/{key}/switch`):
- same harness (other login, endpoint, model, effort): the process is ended, waited for,
  and started again with `--resume <id>` in tmux. Claude logins share `projects/`, which
  is what lets a conversation move between subscriptions; the node refuses if they do not.
  A working session is only interrupted when the owner says so.
- other harness: it cannot load the conversation, so it starts in the same directory with a
  briefing (goal, first and latest request, last words) and the transcript path. The old
  session keeps running unless the owner ticks "end it".

**Logins from the browser.** `POST /api/machines/{m}/logins` creates the login directory
if needed and opens Claude on it in tmux; the web terminal attaches by tmux coordinates
(`<machine>:tmux:<server>:<target>`) so `/login` can be done from the dashboard.

## Consequences

- A switched or dashboard-started interactive session lives in tmux, not in the terminal
  window it came from (`tmux attach -t <name>`).
- A harness that has never seen a folder asks whether to trust it; that pane shows as
  waiting with a pointer to its terminal. The dashboard does not answer that for the owner.
- opencode and Hermes can be started and watched, not pointed at endpoints from here.
