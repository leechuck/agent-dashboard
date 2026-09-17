# Runbook (public skeleton)

The operational runbook for a real deployment lives in `docs/private/`
(gitignored): hostnames, tailnet names, mirror hosts, cutover notes.

Generic operations:

- Deploy a host: `deploy/deploy.sh <ssh-host> hub|node`. Installs user
  systemd units under `~/.config/systemd/user` (linger must be on).
- Publish the hub on a tailnet: `SUDO_PASS=... deploy/tailscale-finish.sh <tailnet>.ts.net`.
- Move the hub: `PW_FILE=... deploy/cutover-hub.sh <new-host> <old-host> <tailnet>`.
- Mirror another machine's session stores for agentsview:
  `deploy/agentsview-mirror.sh <ssh-host>` (timer `agentsview-mirror@<host>.timer`).
- Check a machine: `agentdash doctor`.
- Logs: `journalctl --user -u agentdash-node -f`, `... -u agentdash-hub -f`.
