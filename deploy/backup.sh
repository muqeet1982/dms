#!/bin/bash
# Nightly backup: PostgreSQL dump + media files, keeps N days locally. Optionally rsync to a second host.
set -euo pipefail
DEST=${BACKUP_DIR:-/var/backups/dms}
KEEP=${KEEP_DAYS:-30}
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
DBURL=$(grep '^DATABASE_URL=' /var/www/dms/backend/.env | cut -d= -f2-)
pg_dump "$DBURL" -Fc -f "$DEST/db-$STAMP.dump"
tar -czf "$DEST/media-$STAMP.tar.gz" -C /var/www/dms/backend media 2>/dev/null || true
find "$DEST" -type f -mtime +"$KEEP" -delete
if [ -n "${OFFSITE:-}" ]; then rsync -a --delete "$DEST/" "$OFFSITE/"; fi
echo "backup ok: $DEST/db-$STAMP.dump"
