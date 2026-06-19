#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
BEFORE="$(du -sb /var/cache/apt/archives 2>/dev/null | awk '{print $1}')"
apt-get clean
AFTER="$(du -sb /var/cache/apt/archives 2>/dev/null | awk '{print $1}')"
printf 'before_bytes=%s\n' "${BEFORE:-0}"
printf 'after_bytes=%s\n' "${AFTER:-0}"
printf 'freed_bytes=%s\n' "$(( ${BEFORE:-0} - ${AFTER:-0} ))"
