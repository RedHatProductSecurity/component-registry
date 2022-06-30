#!/usr/bin/env bash

# custom run script for starting corgi celery-beat service in corgi-stage and corgi-prod environments.

# This is only used in docker-compose/podman-compose. In OpenShift deployment
# the migrations are done in celery-beat init container and this is a noop
python3 manage.py migrate --noinput
python3 manage.py migrate collectors --noinput

# Remove any left-over PID files in case the container is being restarted to prevent errors such as:
# ERROR: Pidfile (/tmp/celery_beat.pid) already exists.
rm -f /tmp/celery_beat.pid

# The scheduler process
exec celery -A config beat --loglevel info --pidfile /tmp/celery_beat.pid --scheduler django
