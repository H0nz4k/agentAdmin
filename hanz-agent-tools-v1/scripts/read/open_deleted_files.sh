#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
require_command lsof
lsof +L1 -nP 2>/dev/null | head -n 200
