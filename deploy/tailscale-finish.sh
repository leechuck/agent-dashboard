#!/bin/bash
# Run once both machines are logged into the tailnet and HTTPS + MagicDNS are on.
#   deploy/tailscale-finish.sh <tailnet-domain>      e.g. tail1234.ts.net
# Publishes the hub (443) and agentsview (8443) on ws over HTTPS, points the
# laptop node at the hub over the tailnet, and retires the ssh tunnel.
set -euo pipefail
domain=${1:?tailnet domain, e.g. tailXXXX.ts.net}
: "${SUDO_PASS:?export SUDO_PASS (ws sudo password) for the tailscale serve steps}"
hub="https://ws.$domain"
history="https://ws.$domain:8443"

ssh ws bash -s "$domain" "$history" "$SUDO_PASS" <<'REMOTE'
set -euo pipefail
domain=$1; history=$2; pw=$3
export PATH="$HOME/.local/bin:$PATH"
echo "$pw" | sudo -S -p '' tailscale serve --bg --https=443 http://127.0.0.1:8790 >/dev/null
echo "$pw" | sudo -S -p '' tailscale serve --bg --https=8443 http://127.0.0.1:8080 >/dev/null
grep -q '^public_url' ~/.agentsview/config.toml || printf 'public_url = "%s"\nrequire_auth = false\n' "$history" >> ~/.agentsview/config.toml
sed -i '/^AGENTDASH_HISTORY_PUBLIC_URL=/d' ~/.agentdash/.env
echo "AGENTDASH_HISTORY_PUBLIC_URL=$history" >> ~/.agentdash/.env
# agentsview reaches the laptop by its MagicDNS name from now on
sed -i '/^Host lc-dell$/,/^$/{s/^  HostName .*/  HostName lc-dell/}' ~/.ssh/config
systemctl --user restart agentsview agentdash-hub
sudo -n tailscale serve status || true
REMOTE

sed -i '/^AGENTDASH_HUB_URL=/d' ~/.agentdash/.env
echo "AGENTDASH_HUB_URL=wss://ws.$domain/nodes" >> ~/.agentdash/.env
systemctl --user disable --now agentdash-tunnel.service
systemctl --user restart agentdash-node.service
sleep 5
echo "hub:     $hub"
echo "history: $history"
curl -s -o /dev/null -w "hub reachable over tailnet: HTTP %{http_code}\n" "$hub/healthz"
