#!/usr/bin/env bash

# custom run script for starting corgi celery service in corgi-stage and corgi-prod environments.

# Remove any left-over PID files in case the container is being restarted to prevent errors such as:
# ERROR: Pidfile (/tmp/slow.pid) already exists.
rm -f /tmp/slow.pid

exec celery -A config worker -E --loglevel info --pidfile /tmp/slow.pid -Q slow,fast -n celery@%h
