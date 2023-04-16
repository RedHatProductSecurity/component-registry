#!/usr/bin/env bash

# Custom run script for starting corgi django service in corgi-stage and corgi-prod environments.
# Note - DJANGO_SETTINGS_MODULE env var is required

# collect static files
python3 manage.py collectstatic \
--ignore '*.json' \
-v 2 \
--noinput

# start gunicorn
if [[ $1 == dev ]]; then
    exec gunicorn config.wsgi --config gunicorn_config.py --reload
else
    exec gunicorn config.wsgi --config gunicorn_config.py
fi

