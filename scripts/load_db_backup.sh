#!/usr/bin/env bash
set -e

if ! podman ps | grep 'corgi-db' &>/dev/null; then
    echo 'error: corgi "corgi-db" container does not appear to be running.'
    exit 1
fi

echo "Creating database dump in corgi-db:/tmp"
podman exec -it corgi-db /bin/bash -c 'pg_dump --format=custom -f /tmp/corgi.db corgi-db'

echo "Downloading database dump to $(pwd)"
podman cp corgi-db:/tmp/corgi.db .

echo "Removing database dump from corgi-db:/tmp/corgi.db"
podman exec -it corgi-db /bin/bash -c 'rm -rf /tmp/corgi.db'
