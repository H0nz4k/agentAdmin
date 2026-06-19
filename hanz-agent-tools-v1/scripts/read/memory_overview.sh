#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
printf '=== MEMORY ===\n'
free -h
printf '\n=== PRESSURE ===\n'
for f in /proc/pressure/memory /proc/pressure/cpu /proc/pressure/io; do
  [[ -r "$f" ]] && { printf -- '-- %s --\n' "$f"; cat "$f"; }
done
printf '\n=== SWAP ===\n'
swapon --show --bytes 2>/dev/null || true
printf '\n=== TOP RSS ===\n'
ps -eo pid,ppid,user,comm,rss,%mem,%cpu,etimes,args --sort=-rss \
  | head -n 31
