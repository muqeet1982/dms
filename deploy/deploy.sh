#!/bin/bash
# Deploy / update dms.invoiceflow.pk on this VPS (native: nginx + gunicorn + celery + postgres + redis).
set -euo pipefail
cd /var/www/dms
echo "== backend =="
backend/.venv/bin/python -m pip install -q -r backend/requirements.txt
(cd backend && .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py collectstatic --noinput -v 0)
# every grantable switch needs its Permission row, or role saves silently drop it
(cd backend && .venv/bin/python -c "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from apps.accounts.services import ensure_permission_catalog; ensure_permission_catalog(); print('permission catalog ensured')")
echo "== frontend =="
if [ -d frontend ] && [ -f frontend/package.json ]; then (cd frontend && npm ci --silent && npm run build --silent); fi
echo "== permissions =="
chown -R django:www-data /var/www/dms
chmod 600 backend/.env
echo "== services =="
cp deploy/gunicorn-dms.service deploy/celery-dms.service deploy/celery-beat-dms.service /etc/systemd/system/
cp deploy/nginx-dms.conf /etc/nginx/sites-available/dms
ln -sf /etc/nginx/sites-available/dms /etc/nginx/sites-enabled/dms
systemctl daemon-reload
systemctl enable --now gunicorn-dms celery-dms celery-beat-dms
systemctl restart gunicorn-dms celery-dms celery-beat-dms
nginx -t && systemctl reload nginx
echo "== done: https://dms.invoiceflow.pk =="
