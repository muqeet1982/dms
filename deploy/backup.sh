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
tar -czf "$DEST/media-$STAMP.tar.gz" -C /var/www/dms/backend media 2>/dev/null || true
find "$DEST" -type f -name 'db-*' -mtime +"$KEEP" -delete
find "$DEST" -type f -name 'media-*' -mtime +"$KEEP_MEDIA" -delete
if [ -n "${OFFSITE:-}" ]; then rsync -a --delete "$DEST/" "$OFFSITE/"; fi
echo "backup ok: $DEST/db-$STAMP.dump"
