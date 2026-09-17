Personal briefing for the agent dashboard. Robert reads the result on his phone or in a
browser, approves drafts there, and hands tasks to other agents from there.

Work as the personal assistant (personal-assistant skill, CLAUDE.md in this repo). Then:

1. Gather everything as for a normal briefing: all three message channels (run the
   unread triage so answered mail is dropped), calendar for the next 7 days, deadlines.
   Write the usual `briefings/<today>.md`.

2. For every message that needs a reply from Robert, decide whether you can write the
   reply. If yes and it is a mail: create the reply as a draft in Gnus with
   `claude-email-reply` (new mail: `claude-email-compose`), in Robert's voice, and keep
   the buffer name. DO NOT SEND ANYTHING, on any channel. Robert sends from the dashboard,
   which calls Emacs. For Mattermost and WhatsApp write the proposed text only.
   If you cannot answer without a decision from Robert, say which decision.

3. For everything that is work rather than a reply (edit a paper, review a manuscript,
   fix code, prepare slides, fill a form), create a task. Pick where it runs from
   `configs/workspaces.yaml`: a workspace `name`, or an existing folder below one of the
   `roots` (check that it exists). Write the prompt so that an agent with no access to this
   conversation can do the work: what to do, where the material is (absolute paths,
   message-ids, URLs), the deadline, what done looks like, and what it must not do
   (never send mail, never push to shared branches unless the task says so).
   If a needed file is only an attachment, save it first (under the workspace or
   `~/pa/data/attachments/`) and give the path.

4. Write `data/dashboard_briefing.json` (write to a temporary file in the same folder, then
   rename). UTF-8 JSON, this shape:

{
  "generated_at": "<ISO 8601 with timezone>",
  "summary": "3-5 sentences: what matters today, in order",
  "schedule": [{"when": "<ISO or date>", "what": "...", "note": "clash, travel, prep needed"}],
  "deadlines": [{"date": "YYYY-MM-DD", "what": "...", "project": ""}],
  "items": [
    {
      "id": "<stable: mail:<message-id> | mm:<post id> | wa:<chat jid> | dl:<slug> | task:<slug>>",
      "channel": "mail | mattermost | whatsapp | calendar | deadline | other",
      "urgency": "now | today | week | fyi",
      "from": "Name <address or handle>",
      "subject": "...",
      "received": "<ISO>",
      "ask": "one or two sentences: what they want from Robert",
      "needs_decision": "empty, or the decision Robert has to make before anything can happen",
      "link": "permalink if there is one",
      "draft": null or {
        "kind": "email_reply | email_new | mattermost | whatsapp",
        "buffer": "*claude-mail-N*   (mail only)",
        "message_id": "<id of the mail being answered, no angle brackets>   (email_reply only)",
        "wide": false,
        "to": "...", "cc": "", "subject": "...",
        "body": "the text you wrote, without the quoted original"
      },
      "task": null or {
        "title": "imperative, short",
        "workspace": "<name from workspaces.yaml, or empty>",
        "machine": "lc-dell | ws",
        "path": "<absolute path of the folder to work in>",
        "harness": "claude",
        "prompt": "the self-contained instruction for the other agent",
        "deadline": "YYYY-MM-DD or empty"
      }
    }
  ]
}

   An item may have a draft, a task, both, or neither (fyi). Order items by urgency, then
   date. Keep ids stable between runs so the dashboard remembers what Robert already did.
   Skip anything Robert already handled (status tracked/handled/ignored in the overlays).

5. Finish with one line: how many items, how many drafts, how many tasks. Stay available:
   Robert may send follow-up instructions into this session from the dashboard
   ("reply on Mattermost to X with this text and send it" is an explicit authorization
   for that one message; anything vaguer is not).
