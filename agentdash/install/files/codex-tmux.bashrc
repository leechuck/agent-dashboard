# >>> agentdash: interactive Codex runs inside tmux >>>
# So the agent dashboard can send prompts and /commands to it and show its terminal.
# Non-interactive subcommands, pipes and scripts reach the real binary untouched.
# `command codex ...` bypasses this. Remove the block to undo.
codex() {
  local a
  for a in "$@"; do
    case "$a" in -h|--help|-V|--version) command codex "$@"; return ;; esac
  done
  case "${1:-}" in
    exec|review|login|logout|mcp|plugin|app-server|remote-control|completion|update|doctor|sandbox|debug|apply|archive|unarchive|delete|migrate-rollouts|cloud|exec-server|features|agents|help)
      command codex "$@" ;;
    *)
      if [ -t 0 ] && [ -t 1 ] && [ -z "${TMUX:-}" ] && command -v agent-tmux >/dev/null; then
        agent-tmux codex "$@"
      else
        command codex "$@"
      fi ;;
  esac
}
# <<< agentdash <<<
