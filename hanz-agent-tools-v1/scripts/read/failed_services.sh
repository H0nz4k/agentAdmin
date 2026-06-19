#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
systemctl --failed --no-pager --plain || true
