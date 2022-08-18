#!/usr/bin/env bash
set -m

run-postgresql &

until pg_isready
do
  echo "Waiting for postgres service to be ready..."
  sleep 2;
done
echo "Postgres service started"

echo "Postgres service ready"
cp /pg/postgresql.conf /var/lib/pgsql/data/userdata/postgresql.conf
echo "Postgres configuration files successfully installed"
pg_ctl reload -D /var/lib/pgsql/data/userdata

until pg_isready
do
  echo "Waiting for postgres service to be ready..."
  sleep 2;
done
echo "Postgres service reloaded"

fg
