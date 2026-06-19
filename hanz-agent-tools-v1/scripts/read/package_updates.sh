#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
require_command apt-get
apt-get -s -o Debug::NoLocking=true upgrade 2>/dev/null \
  | sed -n 's/^Inst /UPGRADE /p' \
  | head -n 300
