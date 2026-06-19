#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
TARGET="${1:-}"
CHANGE_ID="${2:-}"
[[ "$CHANGE_ID" =~ ^change-[A-Za-z0-9_.:-]+$ ]] || die "Invalid change id"
TARGET="$(path_is_under_allowlisted_root "$TARGET" /etc/agentAdmin/allowed-backup-paths.txt)"
if path_is_protected "$TARGET" /etc/agentAdmin/protected-paths.txt; then
  die "Protected path cannot be handled by this tool"
fi

BACKUP_ROOT="/var/lib/agentAdmin/backups/$CHANGE_ID"
install -d -m 0700 "$BACKUP_ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="$(basename "$TARGET")"
ARCHIVE="$BACKUP_ROOT/${NAME}-${STAMP}.tar.gz"

tar --one-file-system --xattrs --acls -czf "$ARCHIVE" -C "$(dirname "$TARGET")" "$NAME"
tar -tzf "$ARCHIVE" >/dev/null
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"
printf 'backup=%s\n' "$ARCHIVE"
printf 'size_bytes=%s\n' "$(stat -c '%s' "$ARCHIVE")"
