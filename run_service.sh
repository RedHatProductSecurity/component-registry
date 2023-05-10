#!/usr/bin/env bash

# Custom run script for starting corgi django service in corgi-stage and corgi-prod environments.
# Note - DJANGO_SETTINGS_MODULE env var is required

# start gunicorn
if [[ $1 == dev ]]; then
    exec gunicorn config.wsgi --config gunicorn_config.py --reload
else
    exec gunicorn config.wsgi --config gunicorn_config.py
fi

