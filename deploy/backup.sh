#!/bin/bash
# Nightly backup: PostgreSQL dump + media files, keeps N days locally. Optionally rsync to a second host.
set -euo pipefail
DEST=${BACKUP_DIR:-/var/backups/dms}
KEEP=${KEEP_DAYS:-30}
# Media tarballs run ~0.5 GB a night and filled the 38 GB disk to 94% (26 Oct 2026);
# the DB dumps are a few MB, so only the media retention is shortened.
KEEP_MEDIA=${KEEP_MEDIA_DAYS:-5}
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
DBURL=$(grep '^DATABASE_URL=' /var/www/dms/backend/.env | cut -d= -f2-)
pg_dump "$DBURL" -Fc -f "$DEST/db-$STAMP.dump"
# A media copy only when the files changed: five identical 617 MB nightly copies filled the disk to 100% (12 Nov 2026).
# The fingerprint is every file's path, size and modified time.
FP=$(cd /var/www/dms/backend && find media -type f -printf '%P %s %T@\n' 2>/dev/null | sort | md5sum | cut -d' ' -f1)
if [ "$FP" != "$(cat "$DEST/media.fingerprint" 2>/dev/null || true)" ] || ! ls "$DEST"/media-*.tar.gz >/dev/null 2>&1; then
  if tar -czf "$DEST/media-$STAMP.tar.gz" -C /var/www/dms/backend media 2>/dev/null; then echo "$FP" > "$DEST/media.fingerprint"; else rm -f "$DEST/media-$STAMP.tar.gz"; echo "media copy failed" >&2; fi
else
  echo "media unchanged since the last copy, skipped"
fi
find "$DEST" -type f -name 'db-*' -mtime +"$KEEP" -delete
# old media copies go, but the newest always stays, however old (unchanged media makes no new one)
NEWEST=$(ls -1t "$DEST"/media-*.tar.gz 2>/dev/null | head -1 || true)
find "$DEST" -type f -name 'media-*.tar.gz' -mtime +"$KEEP_MEDIA" ! -path "$NEWEST" -delete
if [ -n "${OFFSITE:-}" ]; then rsync -a --delete "$DEST/" "$OFFSITE/"; fi
echo "backup ok: $DEST/db-$STAMP.dump"
