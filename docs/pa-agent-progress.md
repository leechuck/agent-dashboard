# Personal tab: progress log (branch `pa-view`)

Brief: `BRIEF-pa-agent.md`. Design: `adr/0007-personal-tab-panels-served-by-pa-scripts.md`.
Contract between the node and the PA scripts: `docs/pa-panels.md`.

## Done

- 2026-09-17: read the brief, the reading list and the PA repo; design note written
  (ADR 0007 here, "Dashboard panels" section in the PA repo's CLAUDE.md).

- 2026-09-17: step 1, todo. PA repo: `scripts/todo.py` (list, add, done, reopen, snooze,
  due, due-now) with tests; `todo_sync.py` gives a copied bullet its own id (two live
  duplicates repaired) and honours `PA_ORG_DIR`. Dashboard: `node/pa_panels.py` (script
  runner, validation, cache), `GET /api/pa/panel/{name}`, `POST /api/pa/act`,
  `Personal.svelte` page with `TodoPanel` above the briefing, contract in
  `docs/pa-panels.md`. Tried on a scratch stack (ports 8797/8798, scratch PA copy) at
  1500 px and 412 px.

## Next (in this order)

2. Reminders: node timer, `pa.reminder` event, hub push without storing.
3. Agenda panel and quick add (no attendees, ever).
4. Channels panel, notification log, "show in Ferdium".
5. Hand-off composer, thread export, delegation list with live status.
6. Mail statistics, "waiting on others".

## Open questions for Robert

- PA repo: `tests/test_kg.py` fails because one contact dossier is an unfilled template
  (front matter still has `{{...}}` placeholders). Not touched; fill it or delete it.
- The live todo list has about 70 overdue items, many months old. Reminders therefore
  announce only what becomes due today plus one daily count of the overdue rest, and the
  panel shows six overdue items until "all" is pressed. A clean-out session with the PA
  agent (close, re-date or snooze each) would make the list useful again.
