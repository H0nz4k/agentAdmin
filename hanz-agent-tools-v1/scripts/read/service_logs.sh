#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
SERVICE="${1:-}"
LINES="${2:-200}"
require_simple_id "$SERVICE"
is_allowlisted "$SERVICE" /etc/agentAdmin/allowed-services.txt
[[ "$LINES" =~ ^[0-9]+$ ]] || die "LINES must be numeric"
(( LINES >= 1 && LINES <= 1000 )) || die "LINES must be 1..1000"
journalctl -u "$SERVICE" -n "$LINES" --no-pager --output=short-iso
