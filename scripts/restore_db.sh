#!/usr/bin/env bash
set -e

export PGPASSWORD=test

if [[ -z "$1" ]]; then
    db_dump_dir='./corgi.db'
else
    db_dump_dir="$1"
fi

# ensure there are no extensions in the dump which we can't restore with non-admin permissions
pg_restore -l "${db_dump_dir}" | grep -v "EXTENSION pg_stat_statements" > ./restore-elements

# Restore the database re-creating the tables
pg_restore -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -j 2 -v --no-privileges --no-owner -c -L ./restore-elements "${db_dump_dir}"

rm -rf ./restore-elements
