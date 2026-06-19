#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
printf '=== FILESYSTEMS ===\n'
df -hT -x tmpfs -x devtmpfs
printf '\n=== INODES ===\n'
df -ih -x tmpfs -x devtmpfs
printf '\n=== JOURNAL ===\n'
journalctl --disk-usage 2>/dev/null || true
printf '\n=== DOCKER ===\n'
if command -v docker >/dev/null 2>&1; then
  docker system df 2>/dev/null || true
else
  printf 'docker not installed\n'
fi
