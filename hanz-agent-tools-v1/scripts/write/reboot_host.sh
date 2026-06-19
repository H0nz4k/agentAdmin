#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
CONFIRM="${1:-}"
EXPECTED="REBOOT-$(hostname)"
[[ "$CONFIRM" == "$EXPECTED" ]] || die "Exact confirmation required: $EXPECTED"
sync
systemctl reboot
