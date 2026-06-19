#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
require_command uname
require_command uptime
require_command df
require_command free

printf '=== HOST ===\n'
printf 'hostname=%s\n' "$(hostname -f 2>/dev/null || hostname)"
printf 'kernel=%s\n' "$(uname -srmo)"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
printf '\n=== UPTIME ===\n'
uptime
printf '\n=== LOAD ===\n'
cat /proc/loadavg
printf '\n=== MEMORY ===\n'
free -h
printf '\n=== ROOT FILESYSTEM ===\n'
df -hT /
printf '\n=== INODES ===\n'
df -ih /
printf '\n=== TEMPERATURE ===\n'
if command -v vcgencmd >/dev/null 2>&1; then
  vcgencmd measure_temp || true
elif [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
  awk '{printf "%.1f C\n",$1/1000}' /sys/class/thermal/thermal_zone0/temp
else
  printf 'unavailable\n'
fi
