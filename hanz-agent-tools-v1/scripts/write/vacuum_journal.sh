#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
MAX_SIZE="${1:-500M}"
[[ "$MAX_SIZE" =~ ^[1-9][0-9]{0,4}[KMG]$ ]] || die "Invalid size, e.g. 500M or 1G"
journalctl --disk-usage
journalctl --vacuum-size="$MAX_SIZE"
journalctl --disk-usage
