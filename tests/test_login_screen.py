from agentdash.node import login_screen

METHOD = """
 Claude Code can be used with your Claude subscription or billed based on API usage.

 Select login method:

 ❯ 1. Claude account with subscription · Pro, Max, Team, or Enterprise

   2. Anthropic Console account · API usage billing

   3. 3rd-party platform · Amazon Bedrock, Microsoft Foundry, or Vertex AI
"""

CODE = """
 Browser didn't open? Use the url below to sign in (c to copy)

https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com
%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile&state=abc123

 Hold Shift (Option in iTerm2, Fn in Terminal.app) while selecting to use your terminal's native copy
 Paste code here if prompted >
"""

THEME = """
 To change this later, run /theme
   1. Auto (match terminal)
 ❯ 2. Dark mode ✔
  Syntax theme: Monokai Extended (ctrl+t to disable)
"""


def test_the_login_method_menu_becomes_buttons():
    r = login_screen.read(METHOD)
    assert r["stage"] == "method"
    assert [o["n"] for o in r["options"]] == [1, 2, 3]
    assert r["options"][0]["chosen"] and r["options"][0]["label"].startswith("Claude account")


def test_a_sign_in_address_broken_over_lines_is_joined():
    r = login_screen.read(CODE)
    assert r["stage"] == "code"
    assert r["url"].startswith("https://claude.com/cai/oauth/authorize?code=true")
    assert r["url"].endswith("&state=abc123") and " " not in r["url"]


def test_trust_and_success_are_recognised():
    assert (
        login_screen.read("Is this a project you trust?\n❯ 1. Yes, I trust this folder")["stage"]
        == "trust"
    )
    done = login_screen.read("Login successful. Press Enter to continue…")
    assert done["stage"] == "done" and done["continue"]
    assert login_screen.read("$ ls")["stage"] == "other"


def test_the_first_start_theme_question_is_recognised():
    assert login_screen.read(THEME)["stage"] == "theme"


def test_trust_options_preserve_the_actual_selected_choice():
    screen = "Do you trust this folder?\n❯ 1. No, exit\n  2. Yes, I trust this folder"
    options = login_screen.read(screen)["options"]
    assert options[0] == {"n": 1, "label": "No, exit", "chosen": True}
    assert options[1] == {"n": 2, "label": "Yes, I trust this folder", "chosen": False}


PAUSED = """
● Bash(cd /mnt/data1/DogoHLA; git log --oneline)
  ⎿  Error: Not run: the response that made this tool call was stopped by a safety classifier.
● Fable 5.1's safeguards stopped the response above · continuing once with that noted
────────────────────────────────────────────────────────────
 Session paused
  Fable 5.1's safeguards flagged this message. Our intentionally broad safeguards allow us to
  tasks. Send feedback with /feedback or learn more
  Details: `[bio]`
  ❯ 1. Switch to Opus 5
    2. Edit prompt and retry with Fable 5.1
✻ Waiting for API response · will retry in 2m 40s · check your network
"""

MODEL_SWITCH = """
────────────────────────────────────────────────────────────
 ☐ Model switch
│ Fable 5.1's safeguards flagged this message. Switch to Opus 5 and keep going whenever this happens?
❯ 1. Switch automatically
     Continue on Opus 5 now, and switch without asking from now on
  2. Stay on Fable 5.1
     Stop here without switching, and ask me each time a message is flagged
  3. Type something.
────────────────────────────────────────────────────────────
  4. Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
"""

REPLY_WITH_LIST = """
● Three options:
  1. Keep the symlink check
  2. Resolve the path first
  3. Drop the check
────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────
"""


def test_a_paused_session_dialog_becomes_a_choice():
    r = login_screen.read(PAUSED)
    assert r["stage"] == "choice" and r["title"] == "Session paused"
    assert "flagged this message" in r["question"] and "[bio]" in r["question"]
    assert [(o["n"], o["chosen"]) for o in r["options"]] == [(1, True), (2, False)]
    assert r["options"][0]["label"] == "Switch to Opus 5"


def test_a_model_switch_dialog_becomes_a_choice():
    r = login_screen.read(MODEL_SWITCH)
    assert r["stage"] == "choice" and r["title"] == "Model switch"
    assert [o["n"] for o in r["options"]] == [1, 2, 3, 4]
    assert r["options"][1]["label"] == "Stay on Fable 5.1"


def test_a_numbered_list_in_a_reply_is_not_a_dialog():
    assert login_screen.read(REPLY_WITH_LIST)["stage"] == "other"
