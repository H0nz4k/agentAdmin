#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
SERVICE="${1:-}"
require_simple_id "$SERVICE"
is_allowlisted "$SERVICE" /etc/agentAdmin/allowed-services.txt
systemctl status --no-pager --full "$SERVICE" || true
