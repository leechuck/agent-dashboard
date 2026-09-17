# Personal tab panels: what the node expects from the PA repo

The Personal tab is served by scripts in `<pa_dir>/scripts/` (ADR 0007). The node
(`agentdash/node/pa_panels.py`) runs them with the Python it runs under, in `<pa_dir>`,
without a shell, and reads one JSON object from stdout. This file is the contract; the
scripts and their tests live in the PA repo.

Rules for every script:

- `--json` prints exactly one JSON object with `"ok": true|false`; on failure `"error"` is
  a sentence a person can act on. Exit status non-zero on failure.
- No prompts, no reading from stdin, finishes well inside the timeout listed below.
- Nothing is sent to anybody. Scripts that write (todo, calendar) change only Robert's own
  files and calendars.
- Free text from the browser arrives as ONE argument after `--`; the script treats it as
  data (one line, tags stripped), never as options or markup.

## Panels (`GET /api/pa/panel/{name}`, add `?fresh=true` to skip the node's cache)

| panel | command | cached | timeout |
|---|---|---|---|
| `todo` | `todo.py list --json` | 20 s | 30 s |

### `todo`

```
{ "ok": true, "today": "YYYY-MM-DD",
  "items": [ { "id": "8 hex", "list": "PA", "date": "YYYY-MM-DD", "status": "open",
               "title": "...", "body": "...", "snoozed_until": "" | "YYYY-MM-DD",
               "project": "slug", "project_name": "...", "kind": "research",
               "bucket": "overdue|today|week|later|snoozed", "days": -3 } ],
  "lists": ["PA", ...], "counts": {"overdue": 2, ...},
  "projects": [ {"slug": "...", "name": "...", "kind": "..."} ] }
```

## Actions (`POST /api/pa/act` with `{"act": "...", "args": {...}}`)

The node validates every argument (ids are 8 hex digits, dates `YYYY-MM-DD`, list and
project names by pattern, text by length) before anything is run.

| act | args | command |
|---|---|---|
| `todo_add` | `date`, `text`, `list?`, `project?` | `todo.py add --json --commit --date D --list L [--project P] -- TEXT` |
| `todo_done` | `id`, `note?` | `todo.py done --json --commit [--note N] -- ID` |
| `todo_reopen` | `id` | `todo.py reopen ...` |
| `todo_snooze` | `id`, `until` | `todo.py snooze ... -- ID UNTIL` |
| `todo_unsnooze` | `id` | `todo.py unsnooze ...` |
| `todo_due` | `id`, `date` | `todo.py due ... -- ID DATE` |
| `todo_sync` | | `todo_sync.py sync` (org, Google Tasks, completions pulled back) |

## Trying it without touching real data

Point a development node at a scratch copy: `AGENTDASH_PA_DIR=<scratch>` with a
`CLAUDE.md`, the scripts and a made-up `deadlines.md`, and `PA_ORG_DIR=<scratch>/org` so
the org views are not written into the real org directory.
