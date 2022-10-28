#!/usr/bin/env bash

# Custom run script for starting corgi django service in corgi-stage and corgi-prod environments.
# Note - DJANGO_SETTINGS_MODULE env var is required

# collect static files
python3 manage.py collectstatic \
    --ignore '*.xml' \
    --ignore '*.bz2' \
    --ignore 'tmp*' \
    --noinput

# start gunicorn
exec gunicorn config.wsgi --config gunicorn_config.py
