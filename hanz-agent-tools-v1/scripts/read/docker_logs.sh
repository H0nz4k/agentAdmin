#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
CONTAINER="${1:-}"
LINES="${2:-200}"
require_simple_id "$CONTAINER"
is_allowlisted "$CONTAINER" /etc/agentAdmin/allowed-containers.txt
[[ "$LINES" =~ ^[0-9]+$ ]] || die "LINES must be numeric"
(( LINES >= 1 && LINES <= 1000 )) || die "LINES must be 1..1000"
docker logs --timestamps --tail "$LINES" "$CONTAINER" 2>&1
