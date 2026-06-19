#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
PID="${1:-}"
[[ "$PID" =~ ^[0-9]+$ ]] || die "PID must be numeric"
[[ -d "/proc/$PID" ]] || die "PID not found"
printf '=== PS ===\n'
ps -p "$PID" -o pid,ppid,user,group,lstart,etimes,%cpu,%mem,rss,vsz,stat,comm,args
printf '\n=== CGROUP ===\n'
cat "/proc/$PID/cgroup" 2>/dev/null || true
printf '\n=== LIMITS ===\n'
cat "/proc/$PID/limits" 2>/dev/null || true
printf '\n=== STATUS ===\n'
grep -E '^(Name|State|VmPeak|VmSize|VmRSS|VmSwap|Threads|FDSize):' "/proc/$PID/status" 2>/dev/null || true
