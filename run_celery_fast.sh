#!/usr/bin/env bash

# custom run script for starting corgi celery service in corgi-stage and corgi-prod environments.

# Remove any left-over PID files in case the container is being restarted to prevent errors such as:
# ERROR: Pidfile (/tmp/fast.pid) already exists.
rm -f /tmp/fast.pid

# Reduce the concurrency slightly free up more DB connections for ad-hoc tasks
# Can probably introduce a CONN_MAX_AGE to allow DB connection reuse and therefore higher concurrency
# Probably best to wait to we upgrade to Django 4 or later where we also have CONN_HEALTH_CHECKS
exec celery -A config worker -E --loglevel info --pidfile /tmp/fast.pid -P eventlet -c 3 -Q fast -n celery@%h
