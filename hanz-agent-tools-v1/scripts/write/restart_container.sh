#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
CONTAINER="${1:-}"
require_simple_id "$CONTAINER"
is_allowlisted "$CONTAINER" /etc/agentAdmin/allowed-containers-restart.txt

docker restart --time 20 "$CONTAINER"
sleep 2
docker inspect -f 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}undefined{{end}} restart_count={{.RestartCount}}' "$CONTAINER"
