#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
TARGET="${1:-/}"
MIN_MIB="${2:-100}"
[[ "$MIN_MIB" =~ ^[0-9]{1,6}$ ]] || die "MIN_MIB must be an integer"
TARGET="$(path_is_under_allowlisted_root "$TARGET" /etc/agentAdmin/allowed-paths.txt)"
find "$TARGET" -xdev -type f -size +"${MIN_MIB}"M -printf '%s\t%TY-%Tm-%Td %TH:%TM\t%p\n' 2>/dev/null \
  | sort -nr \
  | head -n 100 \
  | awk -F '\t' '{printf "%.2f GiB\t%s\t%s\n",$1/1073741824,$2,$3}'
