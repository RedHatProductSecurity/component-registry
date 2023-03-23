#!/usr/bin/env bash
set -e

export PGPASSWORD=test

if ! podman ps | grep 'corgi-db' &>/dev/null; then
    echo 'error: corgi "corgi-db" container does not appear to be running.'
    exit 1
fi

pg_dump -v -j 2 -F d -f corgi.db -U corgi-db-user -h localhost -p 5433 corgi-db