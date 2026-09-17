#!/bin/bash
# Incremental mirror of another machine's agent session stores for agentsview.
#   agentsview-mirror.sh <ssh-host>            (mirror lands in ~/agentsview-mirror/<host>/)
# Resumable, excludes bulky non-session data. Followed by a local `agentsview sync`.
set -u
host=${1:?ssh host}
root=~/agentsview-mirror/$host
mkdir -p "$root"
opts=(-az --partial --delete --timeout=60 -e "ssh -o BatchMode=yes -o ConnectTimeout=15")
sync_one() {  # <remote path> <local subdir> [extra rsync args...]
  local src=$1 dst=$2; shift 2
  mkdir -p "$root/$dst"
  rsync "${opts[@]}" "$@" "$host:$src/" "$root/$dst/" 2>&1 | tail -1
}
sync_one .pi/agent/sessions            pi/sessions
sync_one .claude/projects              claude/projects
sync_one .codex/sessions               codex/sessions
sync_one .local/share/opencode/storage opencode/storage --exclude session_diff
sync_one .gemini/tmp                   gemini/tmp --exclude tool-outputs
echo "mirror of $host done $(date -Is)"
~/.local/bin/agentsview sync 2>&1 | tail -2
