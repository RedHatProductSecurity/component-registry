#!/usr/bin/env bash

# custom run script for starting corgi celery service in corgi-stage and corgi-prod environments.

# Remove any left-over PID files in case the container is being restarted to prevent errors such as:
# ERROR: Pidfile (/tmp/cpu.pid) already exists.
rm -f /tmp/cpu.pid

exec celery -A config worker -E --loglevel info --pidfile /tmp/cpu.pid -c 1 -Q cpu -n celery@%h
