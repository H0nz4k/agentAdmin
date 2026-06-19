#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
SERVICE="${1:-}"
require_simple_id "$SERVICE"
is_allowlisted "$SERVICE" /etc/agentAdmin/allowed-services-restart.txt

systemctl restart "$SERVICE"
sleep 2
systemctl is-active "$SERVICE"
systemctl show "$SERVICE" -p ActiveState -p SubState -p NRestarts -p ExecMainStatus
