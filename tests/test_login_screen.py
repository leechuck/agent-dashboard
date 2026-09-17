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
