#!/usr/bin/env bash
set -e

psql -h localhost -p 5455 --user corgi-db-user -d corgi-graph-db < product_node_dump.cypher