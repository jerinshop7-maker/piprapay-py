#!/usr/bin/env bash
# Railway / production entrypoint: migrate, seed-on-first-boot, collectstatic,
# then serve with gunicorn. pp_install is idempotent (no-ops when an admin
# row already exists), so this is safe on every boot/redeploy.
set -euo pipefail

python manage.py migrate --noinput

if [ "${PP_INSTALL_ON_BOOT:-1}" = "1" ]; then
  python manage.py pp_install --noinput \
    --username "${PP_ADMIN_USER:-admin}" \
    --email "${PP_ADMIN_EMAIL:-admin@example.com}" || true
fi

python manage.py collectstatic --noinput --clear

exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --timeout 120 \
  --access-logfile -
