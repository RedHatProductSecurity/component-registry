#!/usr/bin/env bash

# Custom run script for starting corgi django service in corgi-stage and corgi-prod environments.
# Note - DJANGO_SETTINGS_MODULE env var is required

# collect static CSS / JS files like corgi/web/static/base.css
# This is required to avoid breaking the app on startup
# It does not delete any existing files in the static_output dir
python3 manage.py collectstatic --noinput

# start gunicorn
if [[ $1 == dev ]]; then
    exec gunicorn config.wsgi --config gunicorn_config.py --reload
else
    exec gunicorn config.wsgi --config gunicorn_config.py
fi

