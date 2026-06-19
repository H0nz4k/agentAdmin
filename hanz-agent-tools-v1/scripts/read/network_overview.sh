#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
printf '=== ADDRESSES ===\n'
ip -brief address
printf '\n=== ROUTES ===\n'
ip route
printf '\n=== DNS ===\n'
if command -v resolvectl >/dev/null 2>&1; then
  resolvectl status 2>/dev/null | head -n 200
else
  cat /etc/resolv.conf
fi
printf '\n=== LISTENING PORTS ===\n'
ss -lntup
