#!/usr/bin/env bash
set -e

if ! podman ps | grep 'corgi-db' &>/dev/null; then
    echo 'error: corgi "corgi-db" container does not appear to be running.'
    exit 1
fi

echo "Creating database dump in corgi-db:/tmp/dumpdir"
podman exec -it corgi-db /bin/bash -c 'pg_dump -Z0 -j 4 -Fd corgidb -f /tmp/dumpdir'

echo "Compressing database dump to corgi-db:/tpm/corgi-db.tar.gz"
podman exec -it corgi-db /bin/bash -c 'tar cvfz /tmp/corgi-db.tar.gz /tmp/dumpdir'

echo "Downloading database dump to $(pwd)"
podman cp corgi-db:/tmp/corgi-db.tar.gz .

echo "Removing database dump from corgi-db:/tmp"
podman exec -it corgi-db /bin/bash -c 'rm -rf /tmp/corgi-db.tar.gz /tmp/dumpdir'
