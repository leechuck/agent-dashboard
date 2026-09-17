#!/bin/bash
# Move the hub to another tailnet host (e.g. lc2), keeping history on the current host.
#   PW_FILE=<file with the new host's sudo password> deploy/cutover-hub.sh <new-host> <old-hub-host> <tailnet>
# Steps: publish the new hub with tailscale serve, copy VAPID keys and the hub db,
# repoint every node (this machine + old host) at the new hub, retire the old hub.
set -euo pipefail
new=${1:?new hub host, e.g. lc2}; old=${2:?old hub host, e.g. ws}; domain=${3:?tailnet domain}
: "${PW_FILE:?PW_FILE=<path to sudo password file for $new>}"
hub="https://$new.$domain"

ssh "$new" 'sudo -S -p "" tailscale serve --bg --https=443 http://127.0.0.1:8790 >/dev/null' < "$PW_FILE"

# hub state: keys + db (consistent copy via sqlite backup)
ssh "$old" 'systemctl --user stop agentdash-hub; python3 -c "import sqlite3; s=sqlite3.connect(\"$HOME/.agentdash/hub.db\"); d=sqlite3.connect(\"/tmp/hub-copy.db\"); s.backup(d); d.close()"'
ssh "$old" 'cat ~/.agentdash/vapid.json' | ssh "$new" 'cat > ~/.agentdash/vapid.json && chmod 600 ~/.agentdash/vapid.json'
ssh "$old" 'cat /tmp/hub-copy.db' | ssh "$new" 'systemctl --user stop agentdash-hub; cat > ~/.agentdash/hub.db; rm -f ~/.agentdash/hub.db-wal ~/.agentdash/hub.db-shm; systemctl --user start agentdash-hub'
ssh "$old" 'rm -f /tmp/hub-copy.db; systemctl --user disable --now agentdash-hub >/dev/null 2>&1; true'

# repoint nodes
for h in "$old"; do
  ssh "$h" "sed -i '/^AGENTDASH_HUB_URL=/d' ~/.agentdash/.env; echo 'AGENTDASH_HUB_URL=wss://$new.$domain/nodes' >> ~/.agentdash/.env; systemctl --user restart agentdash-node"
done
sed -i '/^AGENTDASH_HUB_URL=/d' ~/.agentdash/.env
echo "AGENTDASH_HUB_URL=wss://$new.$domain/nodes" >> ~/.agentdash/.env
systemctl --user restart agentdash-node
sleep 8
echo "hub: $hub"
curl -s -m 10 "$hub/healthz"; echo
