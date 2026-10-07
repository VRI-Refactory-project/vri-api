#!/bin/sh
# Start script for the Render demo deployment (see render.yaml and DEPLOY.md).
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers 1 \
  --threads 4 \
  --timeout 60
