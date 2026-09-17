# Brief: personal assistant, skill and dashboard view

You own one job: make Robert's personal assistant (the `personal-assistant` skill and the
"Personal" tab of his agent dashboard) much more useful. Another agent owns the rest of the
dashboard and deploys it; you do not deploy.

## Where things are

- PA repo: `~/pa` (= `~/Public/software/pa`, private). `CLAUDE.md` there is authoritative:
  autonomy policy, mail only through Gnus/Emacs, never send without Robert's explicit
  per-message authorization, commits authored as Robert Hoehndorf with no LLM attribution,
  no em-dashes. The skill text is `~/.claude/skills/personal-assistant/SKILL.md` (a copy
  also lives in `~/pa/skill/`; keep them in step). Scripts in `~/pa/scripts/`
  (`gather_briefing.py`, `unread_triage.py`, `mattermost_inbox.py`, `whatsapp_inbox.py`,
  `todo_sync.py` which already syncs `deadlines.md` with org and Google Tasks, `kg.py`, ...).
  `~/pa/configs/workspaces.yaml` lists where which kind of work runs.
- Dashboard: THIS directory is a git worktree of `~/Public/software/agent-dashboard` on
  branch `pa-view`. Work and commit only here, on this branch. Never touch the main
  checkout, never push to `main`, never run `deploy/deploy.sh`, never restart the
  `agentdash-*` systemd units. When a chunk is ready, say so; it gets merged for you.
  Read first: `README.md`, `adr/0005-personal-briefing-through-the-pa-repo.md`,
  `adr/0006-runtimes-endpoints-and-switching.md`, `agentdash/node/pa.py`,
  `agentdash/node/pa_prompt.md`, `agentdash/hub/api.py` (the `/api/pa*` routes relay to the
  node; the hub stores nothing personal), `web/src/components/PersonalBriefing.svelte`,
  `tests/test_pa.py`.
- Dev loop: `uv sync --extra dev`, `uv run pytest -q`, `uv run ruff check .`,
  `cd web && npm ci && npm run build && npx svelte-check`. For a live look run your OWN
  stack on ports 8797/8798 (8795/8796 belong to the other agent), with a throwaway DB:
  `AGENTDASH_HUB_DB=<scratch>/hub.db AGENTDASH_HUB_PORT=8797 AGENTDASH_HUB_HOST=127.0.0.1 AGENTDASH_WEB_TOKEN= AGENTDASH_NODE_TOKEN=t AGENTDASH_HISTORY_URL= uv run agentdash hub`
  and
  `AGENTDASH_HUB_URL=ws://127.0.0.1:8797/nodes AGENTDASH_NODE_TOKEN=t AGENTDASH_NODE_PORT=8798 AGENTDASH_MACHINE_ID=lc-dell uv run agentdash node`.
  Screenshots: `/usr/bin/google-chrome --headless=new --no-sandbox --user-data-dir=<scratch>/prof --window-size=1500,1000 --timeout=9000 --screenshot=x.png <url>`
  (`google-chrome` on PATH is a shim; `--virtual-time-budget` hangs on the event stream).
  Robert reads the dashboard on a phone too: check 412 px width.

## What Robert asked for (his words, 2026-09-17)

"improving the personal-assistant skill and the corresponding PA view on the dashboard; it
should integrate with Google Calendar (gog skill) and other Google services, get stats on
my emails (only local, not through gog), monitor and link to WhatsApp (I use Ferdium),
Mattermost (Ferdium), Slack (Ferdium), and others. Think what would be useful. There also
must be a general todo list (papers, projects) with reminders (maybe from Google todo list
or similar), and a way to communicate with and hand off messages to other agents."

Unpacked:
1. Calendar and other Google services through the `gog` skill: today and the week at a
   glance in the Personal tab, clashes, travel blocks, quick "add reminder / event"; Tasks;
   think about Drive/Docs only where it earns its place.
2. Mail statistics from LOCAL data only (notmuch / Gnus / `~/Mail`), never through gog:
   volume in and out, reply latency, who is waiting on him longest, unanswered by person,
   folders, trends. Decide what changes his behaviour and show that, not vanity numbers.
3. Message channels he reads in Ferdium (WhatsApp, Mattermost, Slack, possibly more):
   monitor what can be monitored locally (the WhatsApp bridge store, the Mattermost API
   script; find out what is possible for Slack without sending anything), show what waits
   for him per channel, and LINK to the right place (can Ferdium be focused on a given
   service or chat from the command line or i3? find out; fall back to permalinks).
4. A general todo list (papers, projects, admin) with reminders, built on what exists
   (`deadlines.md`, `todo_sync.py`, Google Tasks, org files, the `kg/` project graph):
   visible and editable from the Personal tab (add, done, snooze, due date), reminders as
   dashboard push notifications and/or calendar reminders.
5. Talking to other agents from the PA view: hand a message, a task or a whole mail
   thread to a running agent or a new one in the right workspace (the dashboard already
   has `POST /api/sessions/{key}/prompt`, `POST /api/machines/{m}/sessions`, the switch
   and catalog APIs and `workspaces.yaml`), and see what came back.
6. "Think what would be useful": propose more, ranked, before building.

## Hard rules

- Nothing is ever sent (mail, WhatsApp, Mattermost, Slack, calendar invitations to other
  people) by you or by code you write without Robert pressing a button for that one item.
  Never smoke-test with a real send; use drafts and dry runs.
- Personal content stays on the laptop node. The hub relays and stores nothing personal
  (no bodies, no names) in its database. WhatsApp bodies are never committed anywhere.
- Secrets stay in gitignored files; never print or copy keys. Do not read
  `~/.claude/skills/remote-connect/resources/`.
- The dashboard repo is PUBLIC: no personal data, names of correspondents, addresses or
  paths to private material in code, tests, fixtures, docs or commit messages there.
- Do not send prompts into Robert's real sessions as tests; start throwaway ones.
- Commits: author Robert Hoehndorf, no LLM co-author or session trailer.

## How to work

1. Read, then write a short design note first (`adr/0007-...md` on this branch, and a
   section in `~/pa/CLAUDE.md` for the PA side): what you will build, in which order, what
   you decided not to build and why. Keep each step shippable on its own.
2. Build in small commits with tests. Keep a running log in `docs/pa-agent-progress.md`
   (what is done, what is next, open questions for Robert) so anyone can pick it up.
3. Robert may write to you from the dashboard; his messages arrive as "another Claude
   session sent a message". Treat them as his.
4. When you stop, leave the branch green (tests, ruff, svelte-check, web build) and end
   with a plain summary: what works, how to try it, what needs his decision.
