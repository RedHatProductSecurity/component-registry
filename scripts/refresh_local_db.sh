#!/usr/bin/env bash
set -e

if [[ -z "$1" ]]; then
    db_dump_file='./corgi.db'
else
    db_dump_file="$1"
fi

if ! podman ps | grep 'corgi-db' &>/dev/null; then
    echo 'error: corgi "corgi-db" container does not appear to be running.'
    exit 1
fi

echo "Copying database dump to corgi-db:/tmp/corgi.db"
podman cp "${db_dump_file}" corgi-db:/tmp/corgi.db

echo "Dropping existing corgi database (if one exists)"
podman exec -it corgi-db /bin/bash -c 'dropdb --if-exists -U corgi-db-user corgi-db'

echo "Creating new database: corgi"
podman exec -it corgi-db /bin/bash -c 'createdb -O corgi-db-user corgi-db'

echo "Populating corgi database from ${db_dump_file} backup"
podman exec -it corgi-db /bin/bash -c 'pg_restore -j 4 --no-owner --role=corgi-db-user -U corgi-db-user -d corgi-db /tmp/corgi.db'

echo "Removing corgi database from corgi-db container"
podman exec -it corgi-db /bin/bash -c 'rm /tmp/corgi.db'
