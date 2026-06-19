#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
export LC_ALL=C
export PATH='/usr/sbin:/usr/bin:/sbin:/bin'

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

require_simple_id() {
  local value="${1:-}"
  [[ "$value" =~ ^[A-Za-z0-9_.@:-]+$ ]] || die "Invalid identifier: $value"
}

is_allowlisted() {
  local value="$1"
  local file="$2"
  [[ -r "$file" ]] || die "Allowlist not readable: $file"
  grep -Fxq -- "$value" "$file" || die "Not allowlisted: $value"
}

canonical_path() {
  local target="$1"
  realpath -e -- "$target" 2>/dev/null || die "Path does not exist: $target"
}

path_is_under_allowlisted_root() {
  local target="$1"
  local list_file="$2"
  local resolved root
  resolved="$(canonical_path "$target")"
  [[ -r "$list_file" ]] || die "Allowlist not readable: $list_file"

  while IFS= read -r root; do
    [[ -z "$root" || "$root" =~ ^[[:space:]]*# ]] && continue
    root="$(realpath -e -- "$root" 2>/dev/null || true)"
    [[ -n "$root" ]] || continue
    if [[ "$resolved" == "$root" || "$resolved" == "$root/"* ]]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done < "$list_file"

  die "Path is outside allowed roots: $resolved"
}

path_is_protected() {
  local target="$1"
  local list_file="$2"
  local resolved root
  resolved="$(canonical_path "$target")"
  [[ -r "$list_file" ]] || return 1

  while IFS= read -r root; do
    [[ -z "$root" || "$root" =~ ^[[:space:]]*# ]] && continue
    root="$(realpath -e -- "$root" 2>/dev/null || true)"
    [[ -n "$root" ]] || continue
    if [[ "$resolved" == "$root" || "$resolved" == "$root/"* ]]; then
      return 0
    fi
  done < "$list_file"
  return 1
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}
