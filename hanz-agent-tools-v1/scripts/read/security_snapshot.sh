#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
printf '=== SSH LISTENERS ===\n'
ss -lntp | grep -E '(:22[[:space:]]|sshd)' || true
printf '\n=== FAILED LOGINS ===\n'
journalctl -u ssh -u sshd --since '24 hours ago' --no-pager 2>/dev/null \
  | grep -Ei 'failed|invalid|authentication failure' \
  | tail -n 100 || true
printf '\n=== FIREWALL ===\n'
if command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>/dev/null | head -n 300 || true
elif command -v ufw >/dev/null 2>&1; then
  ufw status verbose 2>/dev/null || true
else
  printf 'No supported firewall frontend detected\n'
fi
