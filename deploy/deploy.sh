#!/bin/bash
# Deploy agentdash to a machine over SSH and (re)start its user services.
#   deploy/deploy.sh <ssh-host> hub|node
# Builds the web app locally, rsyncs the tree (no .venv/node_modules), runs
# `uv sync` remotely, installs unit files, restarts services.
set -euo pipefail
host=${1:?ssh host}
role=${2:?hub|node}
here=$(cd "$(dirname "$0")/.." && pwd)
dest='Public/software/agent-dashboard'

if [ "$role" = hub ]; then (cd "$here/web" && npm run build >/dev/null); fi
rsync -az --delete \
  --exclude .venv --exclude node_modules --exclude web/dist --exclude .git \
  --exclude '*.db' --exclude '*.db-*' --exclude .env \
  "$here/" "$host:$dest/"

ssh "$host" bash -s "$role" <<'REMOTE'
set -euo pipefail
role=$1
export PATH="$HOME/.local/bin:$PATH"
cd ~/Public/software/agent-dashboard
uv sync --no-dev -q
mkdir -p ~/.config/systemd/user ~/.agentdash
units="agentdash-node.service"
[ "$role" = hub ] && units="$units agentdash-hub.service agentsview.service agentsview-sync.service agentsview-sync.timer"
for u in $units; do cp deploy/systemd/$u ~/.config/systemd/user/$u; done
systemctl --user daemon-reload
for u in $units; do
  case $u in *.service) [ "$u" = agentsview-sync.service ] || systemctl --user enable --now "$u" >/dev/null 2>&1 || true; systemctl --user restart "$u" 2>/dev/null || true;; *.timer) systemctl --user enable --now "$u" >/dev/null;; esac
done
sleep 2
for u in $units; do printf '%-26s %s\n' "$u" "$(systemctl --user is-active "$u")"; done
REMOTE
