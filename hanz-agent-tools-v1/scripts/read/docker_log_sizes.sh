#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
require_command docker
docker ps -aq | while read -r id; do
  name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')"
  log="$(docker inspect -f '{{.LogPath}}' "$id" 2>/dev/null || true)"
  if [[ -n "$log" && -e "$log" ]]; then
    size="$(stat -c '%s' "$log" 2>/dev/null || echo 0)"
    printf '%s\t%s\t%s\n' "$size" "$name" "$log"
  fi
done | sort -nr | awk -F '\t' '{printf "%.2f GiB\t%s\t%s\n",$1/1073741824,$2,$3}'
