# agentdash design notes

Subject: a control room for one researcher's coding agents (Claude Code, Codex,
pi, opencode) on a laptop and a workstation. Audience: Robert, on an Android
phone at night or on the laptop next to the terminals. Primary job: glance at
what is running, act on the things that need him, know how much budget is left.

## Tokens

Color (light): mist `#F2F4F3` page, `#FFFFFF` surface, ink `#1A222B`, muted
`#5F6B78`, hairline `#D9DEE1`, cobalt `#2450C7` (actions, links), signal
`#B8400B` (needs you), moss `#2D7A4F` (ok/idle), amber `#9A6A00` (limits warn).
Color (dark): slate `#141A21` page, `#1B232C` surface, ink `#E7ECF1`, muted
`#8C98A6`, hairline `#2A343F`, cobalt `#7FA4FF`, signal `#FF9A62`, moss
`#6CC391`, amber `#E0B14C`.

Type: IBM Plex Sans (variable) for everything in the UI; IBM Plex Mono only for
transcript tool input/output and terminal. Scale 13 / 15 / 17 / 22 / 30 with
1.45 line height for body, tight (1.15) for the 30 px number in the attention
strip. Sentence case everywhere. No all-caps labels, no eyebrow labels.

## Layout

Phone first: one column, rows not cards. Each session is a row with a 3 px
status rail on the left (signal = waiting for you, cobalt pulse = busy, hairline
= idle, dashed = offline). Rows are grouped under a sticky machine header that
shows the machine name, node state and its two most important limit bars.

The one memorable element: the attention strip. It exists only while something
waits for Robert. It shows the count as a 30 px number, the first waiting
item's tool call inline in mono, and the answer buttons. Nothing else on the
page competes with it.

Desktop (>= 960 px): roster on the left (360 px), the selected session's
transcript on the right. Same components, no separate layout.

Alignment: left aligned throughout. Numbers right aligned in limit tables.
Radius 6 px on controls only. Shadows none; hierarchy comes from hairlines and
weight.

## Principles

- State is encoded in structure (rail, position, weight), color confirms it.
- Motion only where it answers an action or signals liveness (busy pulse).
- Copy names things by what Robert sees: "waiting for you", "busy", "idle".
- Empty states tell what to do next ("No node connected. Start one with
  `agentdash node` on this machine.").
