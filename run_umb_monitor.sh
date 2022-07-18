#!/usr/bin/env bash

# custom run script for starting corgi umb monitor in corgi-stage and corgi-prod environments.

exec python3 manage.py runmonitor
