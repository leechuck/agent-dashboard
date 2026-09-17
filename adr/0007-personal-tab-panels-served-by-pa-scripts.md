# ADR 0007: The Personal tab is a set of panels, each served by a script in the PA repo

Date: 2026-09-17. Status: accepted (design note for the `pa-view` branch).

## Context

ADR 0005 put the personal briefing into the dashboard: an agent in the PA repo writes one
JSON file, the node reads it, the hub relays. That covers "what came in and what do I
answer", but only after a model run of several minutes, and only as a snapshot. Robert
asked for more on the same tab: the calendar at a glance with quick adds, statistics on
his mail, what waits for him in every message channel with a way to get there, a general
todo list with reminders, and a way to hand messages and work to other agents and see
what came back.

Two constraints shape everything: personal content never rests on the hub, and this
repository is public while the PA repository is private.

## Decision

**The logic lives in the PA repo, the dashboard only calls it.** Every panel is backed by
one script in `<pa>/scripts/` that prints JSON (`--json`), has its own tests there, and is
useful from a terminal and to the PA agent as well. The node runs a script as a
subprocess with an argument list (never a shell), validates what the browser sent before
it becomes an argument, caches read results in memory for a few minutes, and answers
through the existing `pa.request` relay. The hub gets two generic routes,
`GET /api/pa/panel/{name}` and `POST /api/pa/act`, both whitelisted, neither storing
anything. What a script must print is written down in `docs/pa-panels.md`; the tests here
use fake scripts, so no personal datum enters this repository.

Panels, in build order (each one ships alone):

1. **Todo** (`todo.py`): the PA repo's deadline lists are already the source of truth and
   already sync to org-mode and Google Tasks. The panel lists open items grouped overdue,
   today, this week, later, with list and project; add, done, snooze, new due date edit
   the Markdown source and re-run the existing sync. Built first because it is the one
   panel that is also a write path Robert uses many times a day.
2. **Reminders**: the node checks the todo script on a timer (a local file read, no
   network) and sends `pa.reminder` events for items that became due; the hub turns them
   into a web push and, unlike other node events, neither stores nor broadcasts them.
   A reminder for a fixed time of day is a calendar event with a popup instead (next
   point), because the phone's calendar rings even when the laptop is off.
3. **Agenda** (`agenda.py`): today and the next seven days through `gog`, with clashes,
   travel blocks and meetings that end after 17:00 flagged. Quick add creates an event or
   a reminder on Robert's own calendar. The node never passes attendees: an event with
   guests sends invitations, which is sending.
4. **Channels** (`channels.py`): what waits per channel. Mail (unread triage), Mattermost
   (the existing snapshot; refreshed only when Robert presses refresh, as he decided),
   WhatsApp (local bridge store), and everything else Ferdium shows (Slack workspaces,
   Telegram) through a passive log of desktop notifications (`notify_log.py`, a D-Bus
   listener; nothing is polled, no token is used). Links: permalinks where they exist;
   on the laptop a "show in Ferdium" action focuses the Ferdium window and switches to
   the service (`i3-msg` plus the service's Ctrl+number shortcut), since Ferdium has no
   deep links on Linux.
5. **Hand-off**: a composer on the tab sends free text, a briefing item or a whole mail
   thread (exported by the node to a file the prompt points to) to a running agent or a
   new one in a workspace. Every hand-off is recorded in the node's state file, and the
   panel shows the session's live status and last line next to it, with a link.
6. **Mail statistics** (`mail_stats.py`): local notmuch only. Shown: who has been waiting
   for a reply longest, unanswered by person, his median reply time and how it moves,
   mail in and out per week, where the volume lands by folder. Counts that do not change
   what he does next (total archive size, all-time senders) are left out.

## Proposed beyond the request, ranked

1. **Waiting on others**: mails Robert sent that asked something and got no answer in N
   days, with a one-tap "draft a nudge" (a Gnus draft, never sent). Comes nearly free with
   the mail statistics. Will build.
2. **Quick capture**: one box on the phone, "remind me ...", "todo ...", or a note to the
   assistant, routed to todo, calendar or the PA session. Will build as part of 1 and 3.
3. **Meeting prep on the agenda**: the next meeting shows its prep notes from the org
   file and the person's dossier. Will build if time allows; read-only.
4. **Weekly review**: what closed, what slipped, what is new, trend of the mail numbers.
   A prompt for the PA agent rather than code. Later.
5. **Status tiles for reimbursements and sequencing runs** from the scripts that exist.
   Later; cheap.
6. **Drive/Docs**: only as links found in open items. A Drive browser or "recent files"
   list does not change what he does next. Not building.

## Not building, and why

- **Reading Slack with the tokens inside Ferdium's browser profile.** Possible, but it is
  credential extraction from another application, breaks when Slack rotates cookies, and
  acts as him on four workspaces. The notification log gives who and where without it. A
  proper per-workspace Slack app token can be added later if he wants message text.
- **Sending on Mattermost, WhatsApp or Slack from the node.** Unchanged from ADR 0005: the
  only direct send path is mail through Gnus after a button press.
- **A todo database of its own.** A third store next to the Markdown lists and Google
  Tasks would need a third sync. The Markdown lists stay the source.
- **Scheduled polling of Mattermost.** Robert decided against it; the panel shows the age
  of the snapshot and a refresh button.
- **Calendar invitations.** Quick add cannot carry guests.

## Consequences

- The Personal tab works only while the laptop node is online, like the briefing.
- A panel whose script is missing or fails shows its own error; the others still load.
- Reminder texts pass through the hub's memory and the browser push service (encrypted
  end to end by Web Push) but are never written to the hub database or its event log.
- The notification log only sees what Ferdium announces while it runs; a hibernated or
  muted service is silent. The panel says since when the log has been listening.
