#!/bin/bash
# Restore a dump into the live database (stops the app first). Usage: deploy/restore.sh /var/backups/dms/db-YYYYmmdd-HHMMSS.dump
set -euo pipefail
DUMP=$1
DBURL=$(grep '^DATABASE_URL=' /var/www/dms/backend/.env | cut -d= -f2-)
systemctl stop gunicorn-dms celery-dms celery-beat-dms
pg_restore --clean --if-exists --no-owner -d "$DBURL" "$DUMP"
systemctl start gunicorn-dms celery-dms celery-beat-dms
echo "restored $DUMP"
