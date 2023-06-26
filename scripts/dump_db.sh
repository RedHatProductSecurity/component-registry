#!/usr/bin/env bash
set -e

export PGPASSWORD=test

# Use 2 parallel threads to dump the database in directory format
# Using directory format allows us to potentially restore restore to AWS RDS
# Having multiple files allows rsync to work more effectively.

pg_dump --verboase --jobs=2 --format=d --file=corgi.db --username=corgi-db-user --host=localhost --port=5433 corgi-db
