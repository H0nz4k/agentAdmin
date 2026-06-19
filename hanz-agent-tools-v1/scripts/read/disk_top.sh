#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
TARGET="${1:-/}"
DEPTH="${2:-2}"
[[ "$DEPTH" =~ ^[0-4]$ ]] || die "Depth must be 0..4"
TARGET="$(path_is_under_allowlisted_root "$TARGET" /etc/agentAdmin/allowed-paths.txt)"
printf 'Scanning: %s, depth=%s\n' "$TARGET" "$DEPTH"
du -x -B1 --max-depth="$DEPTH" -- "$TARGET" 2>/dev/null \
  | sort -nr \
  | head -n 100 \
  | awk '{printf "%.2f GiB\t%s\n",$1/1073741824,$2}'
