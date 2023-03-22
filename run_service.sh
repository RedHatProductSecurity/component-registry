#!/usr/bin/env bash

# Custom run script for starting corgi django service in corgi-stage and corgi-prod environments.
# Note - DJANGO_SETTINGS_MODULE env var is required

# start gunicorn
if [[ $1 == dev ]]; then
    # collect static files
    python3 manage.py collectstatic \
    --ignore '.gitignore' \
    -v 2 \
    --noinput
    exec gunicorn config.wsgi --config gunicorn_config.py --reload
else
    exec gunicorn config.wsgi --config gunicorn_config.py
fi

